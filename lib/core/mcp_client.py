"""Use a remote MCP server's tools as LlamaIndex `FunctionTool`s (see docs/MCP.md).

An agent that can run its tools either in-process or from an MCP server registers a
toolset::

    FPL_MCP = register_mcp_toolset("fpl", url_env="FPL_MCP_URL", key_env="FPL_MCP_API_KEY",
                                   enabled=lambda: env_str("FPL_TOOLS_SOURCE") == "mcp")

and picks ``FPL_MCP.tools()`` (None until loaded) over its local tools. Agent factories
are sync while listing tools is network I/O, so the engine calls
:func:`refresh_mcp_toolsets` once per turn before it builds the coordinator. That is a
no-op while the cached listing is fresh (``MCP_TOOLS_TTL_SECONDS``, default 300); a
failed refresh keeps the last good listing and waits ``MCP_RETRY_SECONDS`` (default 30)
before trying again, so a down server costs at most one short timeout now and then.

The remote input schema is passed to the model verbatim, and each call opens a short
Streamable HTTP connection (the server is stateless). Failures come back to the agent as
an ``ERROR:`` string, like every other tool failure, rather than raising.
"""

import asyncio
import copy
import logging
import re
import time
from typing import Any, Callable, Optional

import httpx2
from llama_index.core.tools import FunctionTool, ToolMetadata
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict

from lib.core.settings import env_int, env_str

logger = logging.getLogger(__name__)


def _schema_model(tool_name: str, schema: dict) -> type[BaseModel]:
    """A pydantic model whose JSON schema is exactly the server's ``inputSchema``."""
    frozen = copy.deepcopy(schema)

    class RemoteToolArgs(BaseModel):
        model_config = ConfigDict(extra="allow")

        @classmethod
        def model_json_schema(cls, *args, **kwargs) -> dict:  # noqa: D401
            return copy.deepcopy(frozen)

    RemoteToolArgs.__name__ = f"{tool_name}_args"
    return RemoteToolArgs


_KEY_RE = re.compile(r"jvk_[A-Za-z0-9_-]+")


def _redact(text: str) -> str:
    """Hide API keys (the URL may carry one: /mcp/<server>/<key>) in logs and messages."""
    return _KEY_RE.sub("jvk_…", text)


def _describe_error(e: BaseException) -> str:
    """The innermost cause, unwrapped from the SDK's task-group ExceptionGroups."""
    while getattr(e, "exceptions", None):  # (Base)ExceptionGroup; no 3.11 builtin needed
        e = e.exceptions[0]
    return _redact(f"{type(e).__name__}: {e}")


def _result_text(result) -> str:
    parts = [getattr(block, "text", None) for block in (result.content or [])]
    text = "\n".join(p for p in parts if p) or "(empty result)"
    if result.is_error and not text.startswith("ERROR"):
        text = "ERROR: " + text
    return text


class McpToolset:
    """One remote MCP server's tools, cached and exposed as `FunctionTool`s."""

    def __init__(self, name: str, *, url_env: str, key_env: str,
                 enabled: Callable[[], bool], exclude: frozenset[str] = frozenset()):
        self.name = name
        self.url_env = url_env
        self.key_env = key_env
        self._enabled = enabled
        self._exclude = exclude
        self._tools: Optional[list[FunctionTool]] = None
        self._loaded_at: Optional[float] = None
        self._failed_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    # ── config ────────────────────────────────────────────────────────────────
    def enabled(self) -> bool:
        return bool(self._enabled())

    @property
    def url(self) -> Optional[str]:
        return env_str(self.url_env)

    def _http(self, timeout: float) -> httpx2.AsyncClient:
        key = env_str(self.key_env)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return httpx2.AsyncClient(headers=headers, timeout=timeout)

    def _client(self, timeout: float) -> Client:
        return Client(streamable_http_client(self.url, http_client=self._http(timeout)))

    async def _list(self, timeout: float):
        async with self._client(timeout) as client:
            return await client.list_tools()

    async def _call_once(self, tool_name: str, arguments: dict, timeout: float):
        async with self._client(timeout) as client:
            return await client.call_tool(tool_name, arguments)

    # ── state ─────────────────────────────────────────────────────────────────
    def tools(self) -> Optional[list[FunctionTool]]:
        """The remote tools from the last successful listing, or None if never loaded."""
        return self._tools

    def _due(self, now: float) -> bool:
        if self._failed_at is not None and now - self._failed_at < env_int("MCP_RETRY_SECONDS", 30, minimum=1):
            return False
        if self._loaded_at is None:
            return True
        return now - self._loaded_at >= env_int("MCP_TOOLS_TTL_SECONDS", 300, minimum=0)

    async def refresh(self, force: bool = False) -> None:
        if not self.enabled():
            return
        if not self.url:
            if self.last_error is None:
                logger.error("MCP toolset %s enabled but %s is not set", self.name, self.url_env)
            self.last_error = f"{self.url_env} is not set"
            return
        if not force and not self._due(time.monotonic()):
            return
        async with self._lock:
            if not force and not self._due(time.monotonic()):
                return
            timeout = env_int("MCP_LIST_TIMEOUT_SECONDS", 10, minimum=1)
            try:
                listing = await asyncio.wait_for(self._list(timeout), timeout=timeout)
            except Exception as e:  # unreachable, 401, timeout, protocol error…
                self._failed_at = time.monotonic()
                self.last_error = _describe_error(e)
                logger.warning("MCP toolset %s: listing tools failed (%s); %s", self.name, self.last_error,
                               "keeping the previous listing" if self._tools else "no remote tools yet")
                return
            self._tools = [self._as_function_tool(t) for t in listing.tools if t.name not in self._exclude]
            self._loaded_at, self._failed_at, self.last_error = time.monotonic(), None, None
            logger.info("MCP toolset %s: %d tools from %s", self.name, len(self._tools), _redact(self.url))

    # ── calls ─────────────────────────────────────────────────────────────────
    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        timeout = env_int("MCP_CALL_TIMEOUT_SECONDS", 100, minimum=1)
        try:
            result = await asyncio.wait_for(self._call_once(tool_name, arguments, timeout), timeout=timeout)
        except Exception as e:
            reason = "timeout" if isinstance(e, asyncio.TimeoutError) else _describe_error(e)
            logger.warning("MCP call %s.%s failed: %s", self.name, tool_name, reason)
            return (f"ERROR: the {self.name} tool server is unavailable ({reason}). "
                    "Retry at most once; otherwise report that this data is unavailable.")
        return _result_text(result)

    def _as_function_tool(self, remote) -> FunctionTool:
        tool_name = remote.name

        async def _call(**kwargs: Any) -> str:
            return await self.call(tool_name, kwargs)

        _call.__name__ = tool_name
        metadata = ToolMetadata(name=tool_name, description=remote.description or tool_name,
                                fn_schema=_schema_model(tool_name, remote.input_schema or {}))
        return FunctionTool.from_defaults(async_fn=_call, tool_metadata=metadata)


MCP_TOOLSETS: dict[str, McpToolset] = {}


def register_mcp_toolset(name: str, **kwargs) -> McpToolset:
    """Create (once) and register the named toolset; returns the existing one on re-import."""
    if name not in MCP_TOOLSETS:
        MCP_TOOLSETS[name] = McpToolset(name, **kwargs)
    return MCP_TOOLSETS[name]


async def refresh_mcp_toolsets() -> None:
    """Refresh every enabled toolset whose listing is stale (cheap when all are fresh)."""
    due = [t for t in MCP_TOOLSETS.values() if t.enabled()]
    if due:
        await asyncio.gather(*(t.refresh() for t in due))
