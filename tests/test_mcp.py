"""MCP pilot: FPL caller identity, API-key registry, rate limiting, the HTTP gateway and
the Jarvis-side MCP toolset — all hermetic (an in-process ASGI app, no network)."""

import asyncio
import os
import time

import httpx2
import pytest
import yaml
from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context

from lib.agents import FplIdentity, current_fpl_identity, using_fpl_identity
from lib.agents.fpl_agent import agent as fpl_agent_mod
from lib.agents.fpl_agent.identity import resolve_entry_id, resolve_league_id
from lib.core import TOOLS_BY_AGENT, McpToolset
from lib.runtime import mcp_server
from lib.runtime.mcp_keys import (
    McpClient,
    McpClientRegistry,
    McpClientsConfigError,
    RateLimiter,
    hash_key,
    new_key,
    parse_clients,
)


# ── FPL identity ───────────────────────────────────────────────────────────────

def test_identity_order_override_then_bound_then_env(monkeypatch):
    monkeypatch.setenv("FPL_ENTRY_ID", "999")
    monkeypatch.setenv("FPL_LEAGUE_ID", "888")
    assert resolve_entry_id(None) == 999          # nothing bound → env (in-process Jarvis)
    assert resolve_entry_id(5) == 5               # explicit argument always wins
    with using_fpl_identity(FplIdentity(entry_id=1, league_id=2)):
        assert resolve_entry_id(None) == 1
        assert resolve_league_id(None) == 2
        assert resolve_entry_id(7) == 7
    assert current_fpl_identity() is None


def test_bound_identity_never_falls_back_to_owner_env(monkeypatch):
    monkeypatch.setenv("FPL_ENTRY_ID", "999")
    with using_fpl_identity(FplIdentity()):
        assert resolve_entry_id(None) is None     # a friend must not get the owner's team
        assert resolve_league_id(None) is None


def test_fpl_tool_reports_missing_id_for_bound_caller(monkeypatch):
    monkeypatch.setenv("FPL_ENTRY_ID", "999")
    tool = {t.metadata.name: t for t in TOOLS_BY_AGENT["fpl_agent"]}["get_my_fpl_squad"]

    async def run():
        with using_fpl_identity(FplIdentity()):
            return await tool.async_fn(None)

    out = asyncio.run(run())
    assert out["Error"].startswith("No FPL manager id")
    assert "API key" in out["Tip"]


# ── API keys ───────────────────────────────────────────────────────────────────

def _cfg(**clients):
    return {"clients": clients}


def test_keys_are_prefixed_random_and_hash_stably():
    a, b = new_key(), new_key()
    assert a.startswith("jvk_") and a != b and len(a) > 40
    assert hash_key(a) == hash_key(a + "\n") and len(hash_key(a)) == 64


def test_parse_clients_indexes_enabled_clients_by_hash():
    k = new_key()
    by_hash = parse_clients(_cfg(
        marek={"name": "Marek", "key_sha256": hash_key(k), "servers": ["fpl"],
               "fpl": {"entry_id": 12, "league_id": 34}, "rate_limit_per_minute": 5},
        gone={"key_sha256": hash_key(new_key()), "servers": ["fpl"], "enabled": False},
    ), known_servers={"fpl"})
    assert list(by_hash) == [hash_key(k)]
    client = by_hash[hash_key(k)]
    assert client.allows("fpl") and not client.allows("iot")
    assert client.server_settings("fpl") == {"entry_id": 12, "league_id": 34}
    assert client.rate_limit_per_minute == 5


@pytest.mark.parametrize("entry, msg", [
    ({"key_sha256": "abc", "servers": ["fpl"]}, "64-char"),
    ({"key_sha256": "0" * 64, "servers": "fpl"}, "list"),
    ({"key_sha256": "0" * 64, "servers": ["fpl"], "iot": {}}, "unknown key"),
    ({"key_sha256": "0" * 64, "servers": ["fpl"], "fpl": 5}, "mapping"),
    ({"key_sha256": "0" * 64, "servers": ["fpl"], "rate_limit_per_minute": 0}, "positive"),
])
def test_parse_clients_rejects_malformed_entries(entry, msg):
    with pytest.raises(McpClientsConfigError, match=msg):
        parse_clients(_cfg(x=entry), known_servers={"fpl"})


def test_parse_clients_rejects_duplicate_keys():
    h = hash_key(new_key())
    with pytest.raises(McpClientsConfigError, match="duplicate"):
        parse_clients(_cfg(a={"key_sha256": h, "servers": []}, b={"key_sha256": h, "servers": []}))


def test_registry_reloads_on_change_and_keeps_last_good_on_error(tmp_path):
    path = tmp_path / "clients.yaml"
    k1, k2 = new_key(), new_key()
    path.write_text(yaml.safe_dump(_cfg(a={"key_sha256": hash_key(k1), "servers": ["fpl"]})))
    reg = McpClientRegistry(path, known_servers={"fpl"})
    assert reg.authenticate(k1).client_id == "a"
    assert reg.authenticate(k2) is None
    assert reg.authenticate("not-a-jarvis-key") is None

    path.write_text(yaml.safe_dump(_cfg(b={"key_sha256": hash_key(k2), "servers": ["fpl"]})))
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert reg.authenticate(k1) is None          # revoked by removing the block
    assert reg.authenticate(k2).client_id == "b"

    path.write_text("clients: [broken")
    os.utime(path, (time.time() + 10, time.time() + 10))
    assert reg.authenticate(k2).client_id == "b"  # broken file → previous version stays

    path.unlink()
    assert reg.authenticate(k2) is None           # no file → nobody gets in


def test_rate_limiter_sliding_window():
    client = McpClient("c", "C", "0" * 64, frozenset({"fpl"}), rate_limit_per_minute=2)
    rl = RateLimiter(default_per_minute=100)
    assert rl.allow(client, now=0) and rl.allow(client, now=1)
    assert not rl.allow(client, now=2)
    assert rl.allow(client, now=61)


# ── gateway + client, end to end over an in-process ASGI app ──────────────────

async def _echo_identity(ctx: Context, gameweek: int | None = None) -> dict:
    """Echo the bound FPL identity (test tool)."""
    ident = current_fpl_identity()
    return {"entry_id": ident and ident.entry_id, "gameweek": gameweek}


async def _boom(ctx: Context) -> dict:
    """Always fails (test tool)."""
    raise RuntimeError("secret internals")


@pytest.fixture
def mcp_env(monkeypatch, tmp_path):
    """A `demo` exposed server with two fake tools, two clients, and an ASGI app."""
    monkeypatch.setitem(TOOLS_BY_AGENT, "demo_agent", [
        FunctionTool.from_defaults(async_fn=_echo_identity, name="echo_identity"),
        FunctionTool.from_defaults(async_fn=_boom, name="boom"),
    ])
    monkeypatch.setitem(mcp_server.EXPOSABLE, "demo", mcp_server.ExposedServer(
        agent="demo_agent", title="Demo", instructions="test",
        bind_client=mcp_server._fpl_identity))
    keys = {"friend": new_key(), "noaccess": new_key()}
    path = tmp_path / "clients.yaml"
    path.write_text(yaml.safe_dump(_cfg(
        friend={"name": "Friend", "key_sha256": hash_key(keys["friend"]), "servers": ["demo"],
                "fpl": {"entry_id": 42}, "rate_limit_per_minute": 3},
        noaccess={"key_sha256": hash_key(keys["noaccess"]), "servers": []},
    )))
    registry = McpClientRegistry(path, known_servers={"demo", "fpl"})
    app = mcp_server.build_mcp_app(["demo"], registry=registry, limiter=RateLimiter())
    return app, keys


def _asgi_http(app, headers=None):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://mcp.test",
                              headers=headers or {})


def _run_with_app(app, coro_fn):
    async def runner():
        async with app.router.lifespan_context(app):
            return await coro_fn()
    return asyncio.run(runner())


def test_gateway_rejects_bad_keys_and_unknown_servers(mcp_env):
    app, keys = mcp_env
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    accept = {"Accept": "application/json, text/event-stream"}

    async def go():
        async with _asgi_http(app) as h:
            return [
                (await h.get("/mcp/health")).status_code,
                (await h.post("/mcp/demo", json=body, headers=accept)).status_code,
                (await h.post("/mcp/demo/jvk_wrong", json=body, headers=accept)).status_code,
                (await h.post("/mcp/demo", json=body, headers={
                    **accept, "Authorization": f"Bearer {keys['noaccess']}"})).status_code,
                (await h.post("/mcp/iot", json=body, headers=accept)).status_code,
            ]

    assert _run_with_app(app, go) == [200, 401, 401, 403, 404]


def test_unexposable_agents_cannot_be_served():
    with pytest.raises(ValueError, match="not exposable"):
        mcp_server.build_mcp_app(["iot"])


def _toolset(app, url: str, key: str | None, monkeypatch) -> McpToolset:
    monkeypatch.setenv("DEMO_MCP_URL", url)
    if key:
        monkeypatch.setenv("DEMO_MCP_KEY", key)
    ts = McpToolset("demo", url_env="DEMO_MCP_URL", key_env="DEMO_MCP_KEY", enabled=lambda: True)
    ts._http = lambda timeout: httpx2.AsyncClient(  # route the SDK's HTTP through the ASGI app
        transport=httpx2.ASGITransport(app=app),
        headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=timeout)
    return ts


@pytest.mark.parametrize("key_in_url", [False, True])
def test_toolset_lists_and_calls_with_the_keys_identity(mcp_env, monkeypatch, key_in_url):
    app, keys = mcp_env
    url = "http://mcp.test/mcp/demo" + (f"/{keys['friend']}" if key_in_url else "")
    ts = _toolset(app, url, None if key_in_url else keys["friend"], monkeypatch)

    async def go():
        await ts.refresh()
        tools = {t.metadata.name: t for t in ts.tools()}
        echo = (await tools["echo_identity"].acall(gameweek=7)).raw_output
        whoami = (await tools["whoami"].acall()).raw_output
        boom = (await tools["boom"].acall()).raw_output
        return tools, echo, whoami, boom

    tools, echo, whoami, boom = _run_with_app(app, go)
    assert set(tools) == {"echo_identity", "boom", "whoami"}
    schema = tools["echo_identity"].metadata.get_parameters_dict()
    assert "gameweek" in schema["properties"] and "ctx" not in schema["properties"]
    assert '"entry_id": 42' in echo and '"gameweek": 7' in echo
    assert '"client": "friend"' in whoami
    assert boom.startswith("ERROR") and "secret internals" not in boom  # no internals leak


def test_rate_limit_applies_per_client(mcp_env, monkeypatch):
    app, keys = mcp_env
    ts = _toolset(app, "http://mcp.test/mcp/demo", keys["friend"], monkeypatch)

    async def go():
        await ts.refresh()
        echo = {t.metadata.name: t for t in ts.tools()}["echo_identity"]
        return [str((await echo.acall()).raw_output) for _ in range(4)]

    outs = _run_with_app(app, go)
    assert all(o.startswith("{") for o in outs[:3])
    assert "rate limit" in outs[3]


def test_toolset_failure_keeps_previous_listing_and_errors_softly(mcp_env, monkeypatch):
    app, keys = mcp_env
    ts = _toolset(app, "http://mcp.test/mcp/demo", keys["friend"], monkeypatch)

    async def go():
        await ts.refresh()
        good = ts.tools()
        monkeypatch.setenv("DEMO_MCP_KEY", "jvk_revoked")
        ts._http = lambda timeout: httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers={"Authorization": "Bearer jvk_revoked"})
        await ts.refresh(force=True)
        out = (await {t.metadata.name: t for t in ts.tools()}["echo_identity"].acall()).raw_output
        return good, ts.tools(), ts.last_error, out

    good, after, err, out = _run_with_app(app, go)
    assert after is good and err
    assert "jvk_revoked" not in err                       # keys are redacted in errors
    assert out.startswith("ERROR: the demo tool server is unavailable")


# ── fpl_agent source switch ────────────────────────────────────────────────────

def test_fpl_agent_uses_local_tools_by_default(monkeypatch):
    monkeypatch.delenv("FPL_TOOLS_SOURCE", raising=False)
    assert fpl_agent_mod._fpl_tools("fpl_agent") is TOOLS_BY_AGENT["fpl_agent"]
    assert not fpl_agent_mod.FPL_MCP.enabled()


def test_fpl_agent_mcp_source_falls_back_to_local_until_loaded(monkeypatch):
    monkeypatch.setenv("FPL_TOOLS_SOURCE", "mcp")
    monkeypatch.setattr(fpl_agent_mod.FPL_MCP, "_tools", None)
    assert fpl_agent_mod.FPL_MCP.enabled()
    assert fpl_agent_mod._fpl_tools("fpl_agent") is TOOLS_BY_AGENT["fpl_agent"]
    remote = [FunctionTool.from_defaults(async_fn=_boom, name="remote_tool")]
    monkeypatch.setattr(fpl_agent_mod.FPL_MCP, "_tools", remote)
    assert fpl_agent_mod._fpl_tools("fpl_agent") is remote


def test_exposed_fpl_server_lists_every_fpl_tool_without_ctx():
    server = mcp_server.build_tool_server("fpl", RateLimiter())
    assert server.name == "jarvis-fpl"
    for tool in TOOLS_BY_AGENT["fpl_agent"]:
        schema = mcp_server._input_schema(tool)
        assert "ctx" not in schema["properties"]
        assert not mcp_server._description(tool).startswith(tool.metadata.name + "(")
