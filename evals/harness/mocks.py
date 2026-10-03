"""Tool mocking for deterministic, side-effect-free benchmarks.

Why: single/multi-hop (and scripted conversation) evals measure whether the agent *reaches
for* the right tool and composes a good answer — NOT whether the tool itself works (that is a
separate integration-test concern). Running the real tools during a benchmark would (a) cause
real side effects (create reminders, toggle lights, write memory), (b) hit live APIs and cost
money, and (c) inject nondeterminism that makes model comparison unfair.

Mocking keeps the agents' *decisions* intact: each mock ``FunctionTool`` reuses the real
tool's exact metadata (name, description, JSON schema), so the model sees an identical tool
and still decides to call it — our capture records the call for routing/execution metrics.
Only the tool *body* is replaced with a deterministic fixture. Because the fixtures are
realistic, the composer still gets plausible data, so ``composer_quality`` stays meaningful
and fully deterministic (the model is the only variable).

Usage::

    with mock_tools():
        reply = await handle_message(conv_id, text, ctx)   # no real APIs, no side effects
"""

from __future__ import annotations

import contextlib

from llama_index.core.tools import FunctionTool

# Import the aggregator for its registration side effect, then read the registry.
import lib.agents  # noqa: F401  (populates TOOLS_BY_AGENT)
from lib.core import TOOLS_BY_AGENT

# A fixed "now" so date/time-derived answers are reproducible across runs and models.
_NOW = {"date": "2026-08-28", "time": "09:00", "weekday": "piątek", "timezone": "Europe/Warsaw"}


# Realistic, deterministic canned returns keyed by tool name. Read tools return plausible
# data; side-effecting tools (iot/memory/scheduler) return a success confirmation WITHOUT
# doing anything. Shapes mirror the real tools closely enough for the composer to write a
# natural Polish reply.
FIXTURES: dict[str, object] = {
    # --- weather_agent ---
    "get_current_date_and_time": _NOW,
    "current_weather": {
        "city": "Warszawa", "temp_c": 16.0, "feels_like_c": 15.8,
        "description": "bezchmurnie", "wind_kph": 9, "precipitation_mm": 0, "humidity": 55,
    },
    "weather_forecast": {
        "city": "Warszawa",
        "hourly": [
            {"time": "12:00", "temp_c": 19, "description": "słonecznie", "precip_mm": 0},
            {"time": "18:00", "temp_c": 17, "description": "pochmurno", "precip_mm": 0.2},
        ],
        "tomorrow": {"min_c": 12, "max_c": 21, "description": "przelotne opady", "precip_mm": 3},
    },
    # --- finance_agent ---
    "get_exchange_rate": {"pair": "EUR/PLN", "rate": 4.30, "date": _NOW["date"], "source": "Frankfurter"},
    "get_stock_quote": {"symbol": "AAPL", "price": 231.50, "currency": "USD", "change_pct": 0.8},
    "search_ticker": {"query": "WIG20", "matches": [
        {"symbol": "WIG20.WA", "name": "WIG20", "type": "INDEX", "exchange": "WSE", "match_score": 1.0},
    ], "source": "Yahoo Finance search"},
    # --- maps_agent ---
    "get_maps_memory": {"places": ["Dom", "Praca", "Lotnisko Chopina"]},
    "get_route_details": {
        "from": "Dom", "to": "Praca", "mode": "driving",
        "distance_km": 8.4, "duration_min": 22, "summary": "Trasa przez Al. Jerozolimskie",
    },
    # --- news_agent ---
    "search_news": {
        "results": [
            {"title": "Rada Polityki Pieniężnej utrzymuje stopy", "source": "PAP",
             "snippet": "RPP pozostawiła stopy procentowe bez zmian."},
            {"title": "Złoty umacnia się wobec euro", "source": "Bankier",
             "snippet": "EUR/PLN spadł poniżej 4,31 w reakcji na dane inflacyjne."},
        ]
    },
    # --- iot_operator (side effects -> confirmation only) ---
    "get_devices_state": {"salon": "on", "sypialnia": "off", "kuchnia": "off"},
    "get_one_device_status": {"device": "salon", "state": "on", "brightness": 70},
    "turn_on_devices": {"status": "ok", "action": "turned_on", "devices": ["salon"]},
    "turn_off_devices": {"status": "ok", "action": "turned_off", "devices": ["sypialnia"]},
    "change_lighting_mode": {"status": "ok", "mode": "scene"},
    "change_color": {"status": "ok"},
    "change_light_temperature": {"status": "ok", "temperature": "warm", "brightness": 40},
    # --- memory_operator (side effects -> confirmation; read -> canned) ---
    "get_memory": {"preferences": ["kawa bez cukru", "biega rano", "lubi ciepłe światło wieczorem"]},
    "save_memory": {"status": "ok", "saved": True},
    "update_memory": {"status": "ok", "updated": True},
    "delete_memory": {"status": "ok", "deleted": True},
    # --- fpl_agent ---
    "get_fpl_fixtures": {"gameweek": 3, "fixtures": [
        {"home": "Arsenal", "away": "Liverpool", "kickoff": "2026-08-29 18:30", "fdr_home": 4},
    ]},
    "get_pl_teams": {"teams": ["Arsenal", "Liverpool", "Man City", "Chelsea"]},
    "get_my_fpl_squad": {"gameweek": 3, "points": 54, "captain": "Haaland",
                          "bench": ["Rúben Dias"], "value_m": 100.2},
    "get_my_fpl_leagues": {"leagues": [{"name": "Kumple", "rank": 3, "entries": 12}]},
    "get_fpl_league_standings": {"league": "Kumple", "standings": [
        {"rank": 1, "manager": "Tomek", "total": 180}, {"rank": 3, "manager": "Ja", "total": 165}]},
    "get_fpl_live": {"gameweek": 3, "live_points": 42},
    # --- scheduler_agent (side effects -> confirmation only) ---
    "create_scheduled_job": {"status": "ok", "job_id": "mock-job-1", "when": "jutro 08:00"},
    "list_scheduled_jobs": {"jobs": [{"id": "mock-job-1", "when": "jutro 08:00", "task": "parasol"}]},
    "delete_scheduled_job": {"status": "ok", "deleted": True},
    "update_scheduled_job": {"status": "ok", "updated": True},
}


def _fixture_for(name: str) -> object:
    """Deterministic return for a tool; unknown tools get a generic success confirmation."""
    if name in FIXTURES:
        return FIXTURES[name]
    return {"status": "ok", "_mock": name}


def _make_mock(original: FunctionTool) -> FunctionTool:
    """Build a mock tool with identical metadata but an inert, fixture-returning body."""
    name = original.metadata.name

    async def _stub(*args, **kwargs):
        # Ignore all arguments (incl. injected ctx); return the deterministic fixture.
        return _fixture_for(name)

    # Reusing the ORIGINAL metadata keeps the model-facing tool identical (name, description,
    # JSON schema), so tool-selection behaviour is unchanged — only the body is mocked.
    return FunctionTool.from_defaults(async_fn=_stub, tool_metadata=original.metadata)


@contextlib.contextmanager
def mock_tools(agents: list[str] | None = None):
    """Swap every agent's real tools for deterministic mocks for the duration.

    Because the app builds agents fresh each turn from ``TOOLS_BY_AGENT``, replacing the
    registry entries here means any agent built inside the enclosed ``handle_message`` picks
    up the mocks. Originals are restored on exit.

    Args:
        agents: restrict mocking to these agent names; default mocks all registered agents.
    """
    names = agents if agents is not None else list(TOOLS_BY_AGENT.keys())
    saved: dict[str, list] = {}
    try:
        for name in names:
            saved[name] = TOOLS_BY_AGENT[name]
            TOOLS_BY_AGENT[name] = [_make_mock(t) for t in saved[name]]
        yield
    finally:
        for name, original in saved.items():
            TOOLS_BY_AGENT[name] = original
