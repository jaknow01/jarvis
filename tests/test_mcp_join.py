"""Self-service MCP keys: the key store, league-name matching, the /mcp/join page flow
(issue → confirm → replace), and the admin /mcp command. Hermetic: in-process ASGI app,
fake league roster, JSON key store under tmp_path."""

import asyncio
import re

import httpx2
import pytest

from lib.core import McpToolset
from lib.domain import McpKeyStore
from lib.runtime import admin, mcp_server
from lib.runtime.mcp_join import LeagueRoster, match_members
from lib.runtime.mcp_keys import McpClientRegistry, RateLimiter, hash_key, new_key

LEAGUE = 314
ROWS = [
    {"entry": 101, "entry_name": "Łoś Team", "player_name": "Łukasz Nowak", "rank": 1},
    {"entry": 102, "entry_name": "Kane & Able", "player_name": "Marek Kowalski", "rank": 2},
    {"entry": 103, "entry_name": "Ableton FC", "player_name": "Ola Nowak", "rank": 3},
    {"entry": 104, "entry_name": "<script>alert(1)</script>", "player_name": "Hacker", "rank": 4},
]


async def _fake_fetch(league_id, max_managers):
    assert league_id == LEAGUE
    return "Liga Znajomych", ROWS, True


def _members():
    roster = LeagueRoster(fetch_managers=_fake_fetch, league_ids=[LEAGUE])
    return asyncio.run(roster.members())


# ── store ──────────────────────────────────────────────────────────────────────

def test_store_issue_replaces_previous_key_for_the_entry(tmp_path):
    store = McpKeyStore(tmp_path / "keys.json")
    common = dict(entry_id=101, league_id=LEAGUE, manager_name="Ł", team_name="T", servers=["fpl"])
    assert store.issue(key_sha256="a" * 64, **common) == 0
    assert store.issue(key_sha256="b" * 64, **common) == 1
    assert [r["key_sha256"] for r in store.active()] == ["b" * 64]
    assert store.revoke_entry(101) == 1 and store.active() == []
    assert store.revoke_entry(101) == 0


def test_registry_serves_issued_keys_with_their_fpl_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_KEYS_REFRESH_SECONDS", "3600")
    store = McpKeyStore(tmp_path / "keys.json")
    key = new_key()
    store.issue(key_sha256=hash_key(key), entry_id=102, league_id=LEAGUE,
                manager_name="Marek", team_name="Kane", servers=["fpl"])
    reg = McpClientRegistry(tmp_path / "missing.yaml", store=store)
    client = reg.authenticate(key)
    assert client.client_id == "fpl-102" and client.source == "join"
    assert client.server_settings("fpl") == {"entry_id": 102, "league_id": LEAGUE}
    store.revoke_entry(102)
    assert reg.authenticate(key) is not None   # cached until refresh / invalidate
    reg.invalidate()
    assert reg.authenticate(key) is None


# ── matching ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, entries", [
    ("101", [101]),
    ("999", []),
    ("los team", [101]),             # accents/ł-insensitive, exact team name
    ("lukasz nowak", [101]),         # exact manager name
    ("MAREK KOWALSKI", [102]),
    ("nowak", [101, 103]),           # substring → ambiguous
    ("able", [102, 103]),
    ("", []),
])
def test_match_members(query, entries):
    assert sorted(m["entry"] for m in match_members(query, _members())) == entries


# ── page flow ──────────────────────────────────────────────────────────────────

@pytest.fixture
def join_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_LEAGUE_ID", str(LEAGUE))
    monkeypatch.delenv("MCP_JOIN_LEAGUE_IDS", raising=False)
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://jarvis.example")
    monkeypatch.setenv("MCP_KEYS_REFRESH_SECONDS", "3600")
    store = McpKeyStore(tmp_path / "keys.json")
    registry = McpClientRegistry(tmp_path / "none.yaml", known_servers={"fpl"}, store=store)
    notices = []

    async def notify(text):
        notices.append(text)

    app = mcp_server.build_mcp_app(["fpl"], registry=registry, limiter=RateLimiter(), key_store=store,
                                   join_options={"roster": LeagueRoster(fetch_managers=_fake_fetch),
                                                 "notify": notify})
    return app, store, notices


def _run(app, coro_fn):
    async def runner():
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                          base_url="http://mcp.test") as h:
                return await coro_fn(h)
    return asyncio.run(runner())


def _key_in(page: str):
    m = re.search(r"jvk_[A-Za-z0-9_-]{20,}", page)
    return m.group(0) if m else None


async def _whoami(app, key, monkeypatch):
    """Call the whoami tool with ``key`` through the Jarvis-side MCP client."""
    monkeypatch.setenv("J_URL", "http://mcp.test/mcp/fpl")
    ts = McpToolset("j", url_env="J_URL", key_env="J_KEY", enabled=lambda: True)
    ts._http = lambda timeout: httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    return await ts.call("whoami", {})


def test_join_issues_a_working_key_then_confirms_before_replacing(join_app, monkeypatch):
    app, store, notices = join_app
    seen = {}

    async def flow(h):
        seen["form"] = await h.get("/mcp/join")
        seen["missing"] = await h.post("/mcp/join", data={"team": "Nobody United"})
        seen["ambiguous"] = await h.post("/mcp/join", data={"team": "nowak"})
        seen["issued"] = await h.post("/mcp/join", data={"team": "Marek Kowalski"})
        key1 = _key_in(seen["issued"].text)
        seen["who1"] = await _whoami(app, key1, monkeypatch)
        seen["again"] = await h.post("/mcp/join", data={"team": "102"})
        seen["replaced"] = await h.post("/mcp/join", data={"team": "102", "replace": "1"})
        key2 = _key_in(seen["replaced"].text)
        seen["who_old"] = await _whoami(app, key1, monkeypatch)
        seen["who2"] = await _whoami(app, key2, monkeypatch)
        await asyncio.sleep(0)  # let the fire-and-forget notifications run
        return key1, key2

    key1, key2 = _run(app, flow)
    assert seen["form"].status_code == 200 and "<form" in seen["form"].text
    assert seen["missing"].status_code == 404 and _key_in(seen["missing"].text) is None
    assert "Którą drużynę" in seen["ambiguous"].text and _key_in(seen["ambiguous"].text) is None

    issued = seen["issued"]
    assert issued.status_code == 200 and key1
    assert issued.headers["cache-control"] == "no-store"
    assert f"https://jarvis.example/mcp/fpl/{key1}" in issued.text
    assert '"client": "fpl-102"' in seen["who1"] and '"default_entry_id": 102' in seen["who1"]

    assert "ma już klucz" in seen["again"].text and _key_in(seen["again"].text) is None  # no silent replace
    assert key2 and key2 != key1
    assert seen["who_old"].startswith("ERROR")                            # old key revoked
    assert '"client": "fpl-102"' in seen["who2"]
    assert [r["key_sha256"] for r in store.active()] == [hash_key(key2)]
    assert len(notices) == 2 and "Zastąpił poprzedni" in notices[1] and "/mcp revoke 102" in notices[1]


def test_join_escapes_user_controlled_team_names(join_app):
    app, _store, _ = join_app
    page = _run(app, lambda h: h.post("/mcp/join", data={"team": "104"}))
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_join_rate_limit_per_ip(join_app, monkeypatch):
    app, _store, _ = join_app
    monkeypatch.setenv("MCP_JOIN_PER_HOUR", "2")

    async def flow(h):
        hdr = {"X-Forwarded-For": "203.0.113.9"}
        codes = [(await h.post("/mcp/join", data={"team": "x"}, headers=hdr)).status_code for _ in range(3)]
        other = (await h.post("/mcp/join", data={"team": "x"}, headers={"X-Forwarded-For": "198.51.100.1"}))
        return codes, other.status_code

    codes, other = _run(app, flow)
    assert codes == [404, 404, 429] and other == 404


def test_join_disabled_without_league(join_app, monkeypatch):
    app, _store, _ = join_app
    monkeypatch.delenv("FPL_LEAGUE_ID")
    assert _run(app, lambda h: h.get("/mcp/join")).status_code == 503


# ── admin /mcp ─────────────────────────────────────────────────────────────────

def test_admin_mcp_lists_and_revokes(tmp_path, monkeypatch):
    store = McpKeyStore(tmp_path / "keys.json")
    store.issue(key_sha256="c" * 64, entry_id=103, league_id=LEAGUE, manager_name="Ola Nowak",
                team_name="Ableton FC", servers=["fpl"])
    monkeypatch.setattr(admin, "mcp_keys", store)
    monkeypatch.setenv("MCP_CLIENTS_CONFIG", str(tmp_path / "none.yaml"))

    def run(cmd):
        return asyncio.run(admin.maybe_handle(cmd, conversation_id="repl", channel="repl"))

    listing = run("/mcp")
    assert "Ableton FC — Ola Nowak (ID 103)" in listing
    assert "Odwołano" in run("/mcp revoke 103")
    assert store.active() == []
    assert "nie ma aktywnego" in run("/mcp revoke 103")
    assert "Użycie" in run("/mcp revoke abc")
