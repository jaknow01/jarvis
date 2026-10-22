"""Public MCP tool servers: selected agents' tools over Streamable HTTP (see docs/MCP.md).

    POST /mcp/<server>          key in  Authorization: Bearer jvk_…  (or X-API-Key)
    POST /mcp/<server>/<key>    key in the URL — for clients that can't set headers
                                (the Claude apps' custom connectors)
    GET  /mcp/health            liveness, no auth

Only agents listed in :data:`EXPOSABLE` can ever be served. The list lives in code, not
in config, on purpose: owner-only agents (lights, calendar, memory, maps…) must not be
one env var away from the internet. The tools are the same `FunctionTool`s Jarvis runs
in-process (`TOOLS_BY_AGENT`); their docstrings become the MCP tool descriptions.

Per call, the gateway authenticates the key against ``config/mcp_clients.yaml``
(:mod:`lib.runtime.mcp_keys`), checks that the client may use this server, applies a
per-client rate limit, and binds the client's defaults (e.g. its own FPL manager and
league) for the duration of the tool call.
"""

import asyncio
import inspect
import json
import logging
import re
import time
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Optional

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from lib.core import TOOLS_BY_AGENT, env_int
from lib.agents import FplIdentity, using_fpl_identity
from lib.runtime.mcp_keys import McpClient, McpClientRegistry, RateLimiter

logger = logging.getLogger(__name__)

CLIENT_SCOPE_KEY = "jarvis.mcp_client"
_PATH = re.compile(r"^/mcp/(?P<server>[a-z][a-z0-9_-]*)(?:/(?P<key>[^/]+))?/?$")


def _fpl_identity(client: McpClient) -> ContextManager:
    cfg = client.server_settings("fpl")

    def _id(name: str) -> Optional[int]:
        value = cfg.get(name)
        return int(value) if value is not None and str(value).strip().isdigit() else None

    return using_fpl_identity(FplIdentity(entry_id=_id("entry_id"), league_id=_id("league_id")))


@dataclass(frozen=True)
class ExposedServer:
    agent: str
    title: str
    instructions: str
    # Binds the client's per-server defaults around one tool call.
    bind_client: Callable[[McpClient], ContextManager] = lambda _client: nullcontext()
    # Describes the client's defaults for the whoami tool.
    describe_client: Callable[[McpClient], dict] = lambda _client: {}


EXPOSABLE: dict[str, ExposedServer] = {
    "fpl": ExposedServer(
        agent="fpl_agent",
        title="Jarvis FPL",
        instructions=(
            "Fantasy Premier League data from the live FPL API: fixtures with FDR, live "
            "matches, player lookup, a manager's squad, mini-league standings, ownership "
            "and history. 'The owner' in the tool descriptions means the person using this "
            "API key: their default manager id and league are linked to the key (see "
            "whoami). Pass entry_id / league_id explicitly to look at someone else's team "
            "or another league. Do not answer FPL questions from memory — call the tools."
        ),
        bind_client=_fpl_identity,
        describe_client=lambda c: {
            "default_entry_id": c.server_settings("fpl").get("entry_id"),
            "default_league_id": c.server_settings("fpl").get("league_id"),
        },
    ),
}

_WHOAMI = "whoami"


def tool_timeout_seconds() -> int:
    """Wall-clock cap on one tool call (league scans are the slow ones)."""
    return env_int("MCP_TOOL_TIMEOUT_SECONDS", 90, minimum=1)


def _description(tool) -> str:
    """The tool's docstring — without the ``name(signature)`` line LlamaIndex prepends."""
    fn = tool.async_fn or tool.fn
    doc = inspect.getdoc(fn) if fn is not None else None
    return doc or tool.metadata.description


def _input_schema(tool) -> dict:
    schema = dict(tool.metadata.get_parameters_dict())
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def _text_result(payload: Any, is_error: bool = False) -> types.CallToolResult:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


def _client_of(ctx: ServerRequestContext) -> Optional[McpClient]:
    request = ctx.request
    scope = getattr(request, "scope", None) or {}
    return scope.get(CLIENT_SCOPE_KEY)


def build_tool_server(name: str, limiter: RateLimiter) -> Server:
    """The MCP server for one exposed agent's tools."""
    spec = EXPOSABLE[name]
    tools = {t.metadata.name: t for t in TOOLS_BY_AGENT[spec.agent]}
    read_only = types.ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
    listing = [
        types.Tool(name=tool_name, description=_description(t), input_schema=_input_schema(t),
                   annotations=read_only)
        for tool_name, t in tools.items()
    ]
    listing.append(types.Tool(
        name=_WHOAMI,
        description="Shows which client this API key belongs to and the defaults linked to it.",
        input_schema={"type": "object", "properties": {}},
        annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
    ))

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listing)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        client = _client_of(ctx)
        if client is None or not client.allows(name):  # gateway enforces this; belt and braces
            return _text_result("ERROR: not authorized.", is_error=True)
        if params.name == _WHOAMI:
            return _text_result({"client": client.client_id, "name": client.name,
                                 "server": name, **spec.describe_client(client)})
        tool = tools.get(params.name)
        if tool is None:
            return _text_result(f"ERROR: unknown tool {params.name!r}.", is_error=True)
        if not limiter.allow(client):
            logger.warning("MCP rate limit hit: client=%s tool=%s", client.client_id, params.name)
            return _text_result("ERROR: rate limit exceeded — wait a minute before calling again.",
                                is_error=True)
        args = dict(params.arguments or {})
        if tool.requires_context and tool.ctx_param_name:
            # The exposed tools don't use the LlamaIndex workflow Context; never let a
            # caller supply it either.
            args[tool.ctx_param_name] = None
        started = time.monotonic()
        try:
            with spec.bind_client(client):
                result = await asyncio.wait_for(tool.async_fn(**args), timeout=tool_timeout_seconds())
        except asyncio.TimeoutError:
            logger.warning("MCP tool timeout: client=%s tool=%s", client.client_id, params.name)
            return _text_result(f"ERROR: {params.name} did not finish in {tool_timeout_seconds()} s.",
                                is_error=True)
        except TypeError as e:  # bad/unknown arguments
            return _text_result(f"ERROR: invalid arguments for {params.name}: {e}", is_error=True)
        except Exception as e:
            logger.error("MCP tool failed: client=%s tool=%s", client.client_id, params.name, exc_info=True)
            return _text_result(f"ERROR: {params.name} failed ({type(e).__name__}).", is_error=True)
        logger.info("MCP call client=%s server=%s tool=%s %.0fms", client.client_id, name,
                    params.name, (time.monotonic() - started) * 1000)
        return _text_result(result)

    return Server(f"jarvis-{name}", title=spec.title, instructions=spec.instructions,
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def _extract_key(scope: dict, path_key: Optional[str]) -> Optional[str]:
    if path_key:
        return path_key
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key", "").strip() or None


class _Gateway:
    """ASGI: authenticate the key, then hand the request to that server's MCP app."""

    def __init__(self, apps: dict[str, Any], registry: McpClientRegistry):
        self._apps = apps
        self._registry = registry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        match = _PATH.match(scope["path"])
        if not match or match["server"] not in self._apps:
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
            return
        server = match["server"]
        client = self._registry.authenticate(_extract_key(scope, match["key"]))
        if client is None:
            await JSONResponse({"error": "invalid or missing API key"}, status_code=401,
                               headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        if not client.allows(server):
            await JSONResponse({"error": f"this key has no access to '{server}'"},
                               status_code=403)(scope, receive, send)
            return
        # Rewrite to the inner app's single route; never forward the key in the path.
        inner = dict(scope, path="/mcp", raw_path=b"/mcp", root_path="")
        inner[CLIENT_SCOPE_KEY] = client
        await self._apps[server](inner, receive, send)


def build_mcp_app(servers: Optional[list[str]] = None,
                  registry: Optional[McpClientRegistry] = None,
                  limiter: Optional[RateLimiter] = None,
                  key_store: Any = None,
                  join_options: Optional[dict] = None) -> Starlette:
    """The ASGI app serving every exposed server (default: all of :data:`EXPOSABLE`) plus,
    when ``fpl`` is served, the self-service key page ``/mcp/join``.

    ``key_store`` holds the self-service keys (default: ``lib.domain.mcp_keys``);
    ``join_options`` are passed to the join page (tests inject a roster / notifier)."""
    names = servers or list(EXPOSABLE)
    unknown = [n for n in names if n not in EXPOSABLE]
    if unknown:
        raise ValueError(f"not exposable: {unknown} (allowed: {sorted(EXPOSABLE)})")
    if key_store is None:
        from lib.domain import mcp_keys as key_store
    registry = registry or McpClientRegistry(known_servers=set(EXPOSABLE), store=key_store)
    limiter = limiter or RateLimiter()

    # Stateless + JSON responses: every POST is self-contained, so the server keeps no
    # session state and scales/restarts freely. DNS-rebinding protection (a guard for
    # unauthenticated localhost servers) is off: every request needs an API key, and
    # behind the reverse proxy the Host header is the public domain.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp_servers = {n: build_tool_server(n, limiter) for n in names}
    apps = {n: s.streamable_http_app(streamable_http_path="/mcp", stateless_http=True,
                                     json_response=True, transport_security=security)
            for n, s in mcp_servers.items()}

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            for s in mcp_servers.values():
                await stack.enter_async_context(s.session_manager.run())
            logger.info("MCP servers up: %s (clients file: %s)", ", ".join(names), registry.path)
            yield

    async def health(_request):
        return JSONResponse({"ok": True, "servers": names})

    routes: list = [Route("/mcp/health", health)]
    if "fpl" in names:  # the join page hands out fpl keys for the owner's league
        from lib.runtime.mcp_join import build_join_routes
        routes += build_join_routes(registry, key_store, **(join_options or {}))
    routes.append(Mount("/mcp", app=_Gateway(apps, registry)))
    return Starlette(routes=routes, lifespan=lifespan)
