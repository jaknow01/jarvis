"""Use remote MCP servers' tools as LlamaIndex `FunctionTool`s (see docs/MCP.md).

Every tool sub-agent is backed by one :class:`McpToolset` (registered from
``config/subagents.yaml`` by ``lib.agents.mcp_subagents``). Agent factories are sync while
listing tools is network I/O, so the engine calls :func:`refresh_mcp_toolsets` once per turn
before it builds the coordinator: a no-op while the cached listing is fresh
(``MCP_TOOLS_TTL_SECONDS``, default 300); a failed refresh keeps the last good listing and
waits ``MCP_RETRY_SECONDS`` (default 30) before trying again.

Per call:

* **identity** — a toolset declares ``identity`` (``none`` | ``user`` | ``group``); the turn's
  user / household ids are bound with :func:`using_mcp_principal`, and the matching one goes
  out as ``X-On-Behalf-Of`` (honoured by the server only for Jarvis's trusted key);
* **budgets** — ``max_calls_per_turn`` caps calls per tool within one turn (counted on the
  turn's ``Ctx.scratch``); an exhausted budget answers without calling the server;
* **errors** come back to the agent as an ``ERROR:`` string, like every tool failure.

The remote input schema is passed to the model verbatim; each call opens a short Streamable
HTTP connection (servers are stateless). API keys are redacted from logs and messages.
"""

import asyncio
import copy
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

import httpx2
from llama_index.core.tools import FunctionTool, ToolMetadata
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict

from lib.core.cache import app_context
from lib.core.settings import env_int, env_str

logger = logging.getLogger(__name__)

# The HTTP client logs every request URL at INFO — with a key-in-URL server address that
# would put the key in Jarvis's logs. Keep it quiet.
logging.getLogger("httpx2").setLevel(logging.WARNING)

IDENTITY_MODES = ("none", "user", "group")


@dataclass(frozen=True)
class McpPrincipal:
    """Who Jarvis is acting for in this turn (or watch poll)."""

    user_id: Optional[str] = None
    group_id: Optional[str] = None


_PRINCIPAL: ContextVar[Optional[McpPrincipal]] = ContextVar("mcp_principal", default=None)


@contextmanager
def using_mcp_principal(user_id: Optional[str], group_id: Optional[str]) -> Iterator[None]:
    """Bind the acting user/household for MCP calls made inside the block."""
    token = _PRINCIPAL.set(McpPrincipal(user_id, group_id))
    try:
        yield
    finally:
        _PRINCIPAL.reset(token)


def bind_mcp_principal(user_id: Optional[str], group_id: Optional[str]):
    """Token-style binding for code that cannot use a ``with`` block (the engine)."""
    return _PRINCIPAL.set(McpPrincipal(user_id, group_id))


def reset_mcp_principal(token) -> None:
    _PRINCIPAL.reset(token)


_KEY_RE = re.compile(r"jvk_[A-Za-z0-9_-]+")


def _redact(text: str) -> str:
    """Hide API keys (a server URL may carry one: /mcp/<server>/<key>)."""
    return _KEY_RE.sub("jvk_…", text)


def _describe_error(e: BaseException) -> str:
    """The innermost cause, unwrapped from the SDK's task-group ExceptionGroups."""
    while getattr(e, "exceptions", None):
        e = e.exceptions[0]
    return _redact(f"{type(e).__name__}: {e}")


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


def _result_text(result) -> str:
    parts = [getattr(block, "text", None) for block in (result.content or [])]
    text = "\n".join(p for p in parts if p) or "(empty result)"
    if result.is_error and not text.startswith("ERROR"):
        text = "ERROR: " + text
    return text


@dataclass(frozen=True)
class RemoteTool:
    """One tool from a server's listing (kept to rebuild FunctionTools and for evals)."""

    name: str
    description: str
    input_schema: dict
    read_only: Optional[bool] = None


class McpToolset:
    """One remote MCP server's tools, cached and exposed as `FunctionTool`s."""

    def __init__(self, name: str, *, url_env: str, key_env: str,
                 enabled: Callable[[], bool] = lambda: True,
                 exclude: frozenset[str] = frozenset(),
                 identity: str = "none",
                 budgets: Optional[dict[str, int]] = None):
        if identity not in IDENTITY_MODES:
            raise ValueError(f"identity must be one of {IDENTITY_MODES}, got {identity!r}")
        self.name = name
        self.url_env = url_env
        self.key_env = key_env
        self.identity = identity
        self.budgets = dict(budgets or {})
        self._enabled = enabled
        self._exclude = exclude
        self._remote: Optional[list[RemoteTool]] = None
        self._tools: Optional[list[FunctionTool]] = None
        self.instructions: Optional[str] = None
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

    def on_behalf_of(self) -> Optional[str]:
        principal = _PRINCIPAL.get()
        if principal is None or self.identity == "none":
            return None
        return principal.user_id if self.identity == "user" else principal.group_id

    def _headers(self) -> dict[str, str]:
        headers = {}
        key = env_str(self.key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        who = self.on_behalf_of()
        if who:
            headers["X-On-Behalf-Of"] = who
        return headers

    def _http(self, timeout: float) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(headers=self._headers(), timeout=timeout)

    def _client(self, timeout: float) -> Client:
        return Client(streamable_http_client(self.url, http_client=self._http(timeout)))

    async def _list(self, timeout: float):
        async with self._client(timeout) as client:
            return await client.list_tools(), client.instructions

    async def _call_once(self, tool_name: str, arguments: dict, timeout: float):
        async with self._client(timeout) as client:
            return await client.call_tool(tool_name, arguments)

    # ── state ─────────────────────────────────────────────────────────────────
    def tools(self) -> Optional[list[FunctionTool]]:
        """The remote tools from the last successful listing, or None if never loaded."""
        return self._tools

    def remote_tools(self) -> Optional[list[RemoteTool]]:
        return self._remote

    def load_snapshot(self, tools: list[RemoteTool], instructions: Optional[str],
                      call: Optional[Callable[[str, dict], Any]] = None) -> None:
        """Install a listing without a server (evals): ``call(name, args)`` replaces the
        remote call when given."""
        self._remote = list(tools)
        self.instructions = instructions
        self._tools = [self._as_function_tool(t, call) for t in self._remote]
        self._loaded_at, self._failed_at, self.last_error = time.monotonic(), None, None

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
                listing, instructions = await asyncio.wait_for(self._list(timeout), timeout=timeout)
            except Exception as e:  # unreachable, 401, timeout, protocol error…
                self._failed_at = time.monotonic()
                self.last_error = _describe_error(e)
                logger.warning("MCP toolset %s: listing tools failed (%s); %s", self.name, self.last_error,
                               "keeping the previous listing" if self._tools else "no remote tools yet")
                return
            remote = [
                RemoteTool(name=t.name, description=t.description or t.name,
                           input_schema=dict(t.input_schema or {}),
                           read_only=getattr(t.annotations, "read_only_hint", None) if t.annotations else None)
                for t in listing.tools if t.name not in self._exclude
            ]
            self.load_snapshot(remote, instructions)
            logger.info("MCP toolset %s: %d tools from %s", self.name, len(remote), _redact(self.url))

    # ── calls ─────────────────────────────────────────────────────────────────
    def _spend_budget(self, tool_name: str) -> Optional[str]:
        """None when the call may proceed; else the budget-exhausted message."""
        limit = self.budgets.get(tool_name)
        if not limit:
            return None
        try:
            scratch = app_context().scratch
        except LookupError:
            return None  # no turn context (direct/test use): unbounded
        key = f"mcp_budget.{self.name}.{tool_name}"
        used = scratch.get(key, 0)
        if used >= limit:
            return (f"ERROR: the {tool_name} budget for this request is used up ({limit}). Stop calling "
                    "it and answer from what you already have; say which part could not be checked.")
        scratch[key] = used + 1
        return None

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if exhausted := self._spend_budget(tool_name):
            return exhausted
        timeout = env_int("MCP_CALL_TIMEOUT_SECONDS", 100, minimum=1)
        try:
            result = await asyncio.wait_for(self._call_once(tool_name, arguments, timeout), timeout=timeout)
        except Exception as e:
            reason = "timeout" if isinstance(e, asyncio.TimeoutError) else _describe_error(e)
            logger.warning("MCP call %s.%s failed: %s", self.name, tool_name, reason)
            return (f"ERROR: the {self.name} tool server is unavailable ({reason}). "
                    "Retry at most once; otherwise report that this data is unavailable.")
        return _result_text(result)

    def _as_function_tool(self, remote: RemoteTool, call: Optional[Callable] = None) -> FunctionTool:
        tool_name = remote.name

        async def _call(**kwargs: Any) -> str:
            if call is not None:
                if exhausted := self._spend_budget(tool_name):
                    return exhausted
                out = call(tool_name, kwargs)
                return await out if asyncio.iscoroutine(out) else out
            return await self.call(tool_name, kwargs)

        _call.__name__ = tool_name
        metadata = ToolMetadata(name=tool_name, description=remote.description,
                                fn_schema=_schema_model(tool_name, remote.input_schema))
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
