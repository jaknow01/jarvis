"""cluster_agent plumbing: Unix-socket toolsets, admin-only tools kept from the model, the
generic `/k` admin command, and sensitive turns (gating, no logs, placeholder in history)."""

import asyncio
import os
import shutil
import tempfile

import pytest

from lib.agents import MCP_SUBAGENTS, toolset_for
from lib.agents.mcp_subagents import factory, sensitive_allowed
from lib.agents.mcp_subagents.config import SubagentsConfigError, parse_subagents
from lib.core import ADMIN_META_KEY, McpToolset, RemoteTool
from lib.runtime import admin, engine
from lib.runtime.reply import Reply, loggable
from tests.conftest_mcp import listed_subagents

# ── config ─────────────────────────────────────────────────────────────────────


def test_shipped_cluster_agent_config():
    cfg = MCP_SUBAGENTS["cluster_agent"]
    assert cfg.sensitive and cfg.uds_env == "MCP_CLUSTER_UDS" and cfg.admin_command == "k"
    assert cfg.key_env == "MCP_CLUSTER_KEY" and cfg.admin_key_env == "MCP_CLUSTER_ADMIN_KEY"
    assert MCP_SUBAGENTS["fpl_agent"].admin_key_env == "MCP_FPL_ADMIN_KEY"
    assert "whoami" in factory.EXCLUDED_TOOLS and "revoke_issued_key" not in factory.EXCLUDED_TOOLS


@pytest.mark.parametrize("entry, msg", [
    ({"description": "d", "key_env": "K"}, "url_env"),
    ({"description": "d", "uds_env": "S", "key_env": "K", "admin_key_env": "K"}, "must differ"),
    ({"description": "d", "uds_env": "S", "key_env": "K", "admin_command": "k"}, "admin_key_env"),
    ({"description": "d", "uds_env": "S", "key_env": "K", "admin_key_env": "A", "admin_command": "K!"}, "lowercase"),
    ({"description": "d", "uds_env": "S", "key_env": "K", "sensitive": "yes"}, "true/false"),
])
def test_config_validation(entry, msg):
    with pytest.raises(SubagentsConfigError, match=msg):
        parse_subagents({"subagents": {"x": entry}})


def test_socket_entry_needs_no_url():
    (cfg,) = parse_subagents({"subagents": {"x": {"description": "d", "uds_env": "S", "key_env": "K"}}})
    assert cfg.url_env == "" and cfg.uds_env == "S"


# ── gating ─────────────────────────────────────────────────────────────────────


def test_sensitive_agent_off_unless_explicitly_on_and_untraced(monkeypatch):
    monkeypatch.delenv("AGENT_CLUSTER_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert not sensitive_allowed("cluster_agent")  # default OFF, unlike other agents
    monkeypatch.setenv("AGENT_CLUSTER_AGENT_ENABLED", "true")
    assert sensitive_allowed("cluster_agent")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    monkeypatch.setenv("TRACING_ENABLED", "true")
    assert not sensitive_allowed("cluster_agent")
    monkeypatch.setenv("TRACING_ENABLED", "false")
    assert sensitive_allowed("cluster_agent")


def _coordinator_tools(**kwargs):
    from lib.agents import create_coordinator_agent

    with listed_subagents():
        return {t.metadata.name for t in create_coordinator_agent(**kwargs).tools}


def test_coordinator_roster_for_sensitive_agent(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("AGENT_CLUSTER_AGENT_ENABLED", raising=False)
    assert "cluster_agent" not in _coordinator_tools()
    monkeypatch.setenv("AGENT_CLUSTER_AGENT_ENABLED", "1")
    assert "cluster_agent" in _coordinator_tools()
    assert "cluster_agent" not in _coordinator_tools(allow_sensitive=False)  # scheduled turns


# ── Unix socket + admin-only tools ─────────────────────────────────────────────


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="jv", dir="/tmp")  # AF_UNIX paths are short
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _serve_on_socket(path: str):
    """A plain MCP server on a Unix socket: one normal tool, one marked admin-only."""
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("demo", instructions="Demo guide.")

    async def status() -> str:
        """Read-only status."""
        return "all good"

    async def restart(name: str) -> str:
        """Admin: restart something."""
        return f"restarted {name}"

    server.add_tool(status, name="status")
    server.add_tool(restart, name="restart", meta={ADMIN_META_KEY: True})
    from mcp.server.transport_security import TransportSecuritySettings

    app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return uvicorn.Server(uvicorn.Config(app, uds=path, log_config=None, access_log=False))


def test_toolset_over_unix_socket_drops_admin_tools(sock_dir, monkeypatch):
    path = os.path.join(sock_dir, "mcp.sock")
    monkeypatch.setenv("TEST_SOCK", path)
    monkeypatch.setenv("TEST_KEY", "jvk_test")
    agent_ts = McpToolset("demo", url_env="", key_env="TEST_KEY", uds_env="TEST_SOCK")
    assert agent_ts.url == "http://localhost/mcp" and agent_ts.enabled()

    async def go():
        server = _serve_on_socket(path)
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        try:
            await agent_ts.refresh(force=True)
            admin_tools = await agent_ts.list_admin_tools()
            out = await agent_ts.call("status", {})
            admin_out = await agent_ts.call("restart", {"name": "x"})
        finally:
            server.should_exit = True
            await task
        return admin_tools, out, admin_out

    admin_tools, out, admin_out = asyncio.run(go())
    assert [t.metadata.name for t in agent_ts.tools()] == ["status"]        # admin tool never given to an agent
    assert [t.name for t in admin_tools] == ["restart"] and admin_tools[0].admin_only
    assert agent_ts.instructions == "Demo guide." and out == "all good"
    assert admin_out == "restarted x"


def test_missing_socket_means_server_unavailable(sock_dir, monkeypatch):
    monkeypatch.setenv("TEST_SOCK", os.path.join(sock_dir, "absent.sock"))
    monkeypatch.setenv("TEST_KEY", "jvk_test")
    ts = McpToolset("absent", url_env="", key_env="TEST_KEY", uds_env="TEST_SOCK")
    asyncio.run(ts.refresh(force=True))
    assert ts.tools() is None and "not found" in ts.last_error
    assert asyncio.run(ts.call("status", {})).startswith("ERROR: the absent tool server is unavailable")


# ── /k ─────────────────────────────────────────────────────────────────────────


class FakeAdminToolset:
    def __init__(self):
        self.calls = []

    async def list_admin_tools(self):
        return [
            RemoteTool("restart", "Admin: rolling restart of a deployment.\nMore.",
                       {"type": "object", "properties": {"deployment": {"type": "string"}},
                        "required": ["deployment"]}, admin_only=True),
            RemoteTool("logs", "Admin: last log lines.",
                       {"type": "object", "properties": {
                           "name": {"type": "string"},
                           "lines": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}}, admin_only=True),
        ]

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        return f"ok {tool}"


@pytest.fixture
def fake_admin(monkeypatch):
    fake = FakeAdminToolset()
    import lib.agents

    monkeypatch.setattr(lib.agents, "admin_toolset_for", lambda name: fake if name == "cluster_agent" else None)
    return fake


def _k(text, is_admin=True):
    return asyncio.run(admin.maybe_handle(text, conversation_id="t", channel="test", is_admin=is_admin))


def test_k_lists_admin_tools(fake_admin):
    reply = _k("/k")
    assert isinstance(reply, Reply) and reply.sensitive
    assert "/k restart <deployment> — rolling restart of a deployment." in reply
    assert "/k logs [name] [lines] — last log lines." in reply
    assert fake_admin.calls == []


def test_k_maps_positional_args_and_coerces_types(fake_admin):
    assert _k("/k restart mcp-fpl") == "ok restart"
    assert _k("/k logs mcp-fpl 50") == "ok logs"
    assert fake_admin.calls == [("restart", {"deployment": "mcp-fpl"}), ("logs", {"name": "mcp-fpl", "lines": 50})]


@pytest.mark.parametrize("text, msg", [
    ("/k restart a b", "Za dużo argumentów"),
    ("/k logs x many", "Zły typ"),
    ("/k nuke", "Nieznana komenda"),
])
def test_k_refuses_bad_input_without_calling(fake_admin, text, msg):
    assert msg in _k(text)
    assert fake_admin.calls == []


def test_k_is_admin_only(fake_admin):
    assert _k("/k restart mcp-fpl", is_admin=False) is None  # falls through like any non-admin /text
    assert fake_admin.calls == []


def test_k_without_admin_key(monkeypatch):
    monkeypatch.delenv("MCP_CLUSTER_ADMIN_KEY", raising=False)
    assert "brak klucza admina (MCP_CLUSTER_ADMIN_KEY)" in _k("/k")


def test_help_mentions_server_commands():
    assert "/k [komenda …] — komendy admina serwera cluster_agent" in _k("/help")


# ── sensitive turns ────────────────────────────────────────────────────────────


def test_loggable_hides_sensitive_content():
    assert loggable(Reply("pod x crashed", sensitive=True)) == "<treść poufna pominięta, 13 znaków>"
    assert loggable(Reply("hello")) == "hello" and loggable("plain") == "plain"


class _Handler:
    def __init__(self, answer):
        self._answer = answer

    def __await__(self):
        async def done():
            return self._answer
        return done().__await__()

    async def stream_events(self):
        if False:
            yield None


@pytest.mark.parametrize("called, sensitive", [(["cluster_agent"], True), (["weather_agent"], False)])
def test_sensitive_turn_is_stored_as_placeholder(monkeypatch, called, sensitive):
    from lib.core import app_context

    recorded = []

    class Coordinator:
        def run(self, **kwargs):
            app_context().subagent_calls.extend(called)
            return _Handler("Pod mcp-fpl restartował się 4 razy.")

    class Composer:
        async def run(self, **kwargs):
            return "Pod mcp-fpl restartował się 4 razy."

    async def no_history(_cid):
        return []

    async def no_refresh():
        return None

    monkeypatch.setattr(engine, "create_coordinator_agent", lambda profile, allow_sensitive=True: Coordinator())
    monkeypatch.setattr(engine, "create_composer_agent", lambda: Composer())
    monkeypatch.setattr(engine, "build_chat_history", no_history)
    monkeypatch.setattr(engine, "refresh_mcp_toolsets", no_refresh)
    monkeypatch.setattr(engine, "schedule_summary_fold", lambda cid: None)
    monkeypatch.setattr(engine.conversations, "record_turn",
                        lambda cid, user, assistant, trace_id="": recorded.append((user, assistant)))

    from lib.core import Ctx

    reply = asyncio.run(engine.handle_message("test:1", "co z klastrem?", Ctx(cache=None)))
    assert reply == "Pod mcp-fpl restartował się 4 razy." and reply.sensitive is sensitive
    ((user, stored),) = recorded
    assert user == "co z klastrem?"
    assert (stored == engine.SENSITIVE_HISTORY_PLACEHOLDER) is sensitive
