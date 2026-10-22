"""Named data-source registry for conditional watches (three-state reads).

Maps a ``source-id`` to a deterministic fetcher returning a ``dict``. A watch source is
just one of the assistant's **read-only tools** (on its MCP server) reused as a pollable
data feed — **no LLM/agent run is involved**, only the tool call. The catalog
is therefore intended to be *as broad as the assistant's data tools*: adding a new source
is one row in ``_WATCHABLE`` (a read-only tool → a source-id + a short predicate-writing
hint), not a bespoke fetcher. Mutating tools (smart-home on/off, memory writes, the
scheduler itself) are deliberately **not** exposed — a watch polls, it never acts.

``fetch_source`` wraps a fetch into a three-state result:

    ("ok", data)        - a clean data dict
    ("unknown", None)   - unknown source, the fetch raised, or it returned an error dict
                          (e.g. {"Error": ...}); never mistaken for a `false` predicate

Each source calls a tool on the corresponding sub-agent's MCP server (config/subagents.yaml),
forwarding the acting user/household bound with ``using_mcp_principal``. This module is
consumed both by the watch tools (agents) and the scheduler runner (runtime). The catalog
of valid source-ids is **owned here** (dynamic, derived from the registered tools) and is
the single source of truth for source validation and for what the model is told exists.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

Fetcher = Callable[[dict], Awaitable[dict]]

# source-id -> async fetcher(params) -> dict, and a parallel id -> human/model description
# (params + the kind of data a predicate can read). Kept parallel so ``register_source``
# stays a 2-arg call for tests/ad-hoc sources; the description defaults to "".
SOURCES: dict[str, Fetcher] = {}
DESCRIPTIONS: dict[str, str] = {}


def register_source(source_id: str, fetcher: Fetcher, description: str = "") -> None:
    SOURCES[source_id] = fetcher
    DESCRIPTIONS[source_id] = description


def known_sources() -> list[str]:
    return sorted(SOURCES)


def is_known(source_id: str) -> bool:
    return source_id in SOURCES


def describe_sources() -> dict[str, str]:
    """id -> short description (params + what data a predicate can read). Used to tell the
    model which watch sources exist and how to write a predicate for each."""
    return dict(DESCRIPTIONS)


def _is_error(data) -> bool:
    return isinstance(data, dict) and "Error" in data


async def fetch_source(source_id: str, params: Optional[dict]) -> tuple[str, Optional[dict]]:
    """Fetch ``source_id`` with ``params`` as a three-state read. Never raises.

    Returns ``("ok", data)`` on a clean dict, or ``("unknown", None)`` when the source is
    unknown, the fetch raises, or it returns an error dict.
    """
    fetcher = SOURCES.get(source_id)
    if fetcher is None:
        logger.error("watch source '%s' is not registered", source_id)
        return "unknown", None
    try:
        data = await fetcher(params or {})
    except Exception as e:  # noqa: BLE001 - a fetch failure is 'unknown', never a crash
        logger.warning("watch source '%s' fetch failed: %s", source_id, e)
        return "unknown", None
    if _is_error(data):
        logger.info("watch source '%s' returned an error result", source_id)
        return "unknown", None
    return "ok", data


# --- sources: reuse existing (ctx-free) read-only subagent tools -----------------
#
# One row per source: (source-id, agent-name, tool-name, predicate-writing hint). The hint
# names the params and the salient fields a predicate reads — it is what the model is shown
# to author `check_code`; the exact key names are additionally confirmed at authoring time by
# the live data sample prepare_watch returns. To broaden the catalog, add a row for any
# read-only tool. Only NON-mutating tools belong here.
_WATCHABLE: list[tuple[str, str, str, str]] = [
    ("finance.fx", "finance_agent", "get_exchange_rate",
     "Currency exchange rate. params: {foreign_currency:'USD', base_currency:'PLN' (opt)}. "
     "Data includes the numeric 'rate'."),
    ("finance.quote", "finance_agent", "get_stock_quote",
     "Stock/index quote. params: {symbol:'AAPL' or 'PKN.WA' for GPW}. Data includes the "
     "latest price."),
    ("weather.current", "weather_agent", "current_weather",
     "Current weather. params: {city:'Warsaw' (opt)}. Data includes temperature, conditions, "
     "wind, etc."),
    ("weather.forecast", "weather_agent", "weather_forecast",
     "Weather forecast. params: {city, ...}. Data includes upcoming per-period temp / rain / "
     "conditions."),
    ("fpl.live", "fpl_agent", "get_fpl_live",
     "Live gameweek state. params: {gameweek (opt), entry_id (opt)}. Data includes "
     "'any_live', 'live_matches' (home/away/score/minutes/events) and, for the owner, "
     "'my_players' with live per-player points/goals/minutes. Use for 'when my player scores' "
     "type conditions — but write the edge carefully (see the intent gate)."),
    ("fpl.player", "fpl_agent", "get_fpl_player",
     "One FPL player's data. params: {name:'Salah', upcoming_fixtures (opt)}. Data includes "
     "price, form, points and upcoming fixtures/FDR."),
    ("fpl.fixtures", "fpl_agent", "get_fpl_fixtures",
     "Fixtures + FDR for a gameweek. params: {gameweek (opt)}."),
    ("fpl.league_standings", "fpl_agent", "get_fpl_league_standings",
     "Mini-league standings. params: {league_id (opt), ...}. Data includes each manager's "
     "rank and points."),
    ("news.search", "news_agent", "search_news",
     "News/finance search. params: {query, topic:'news'|'finance', ...}. Data includes recent "
     "matching articles — a predicate can test for the appearance of a matching result."),
    ("maps.route", "maps_agent", "get_route_details",
     "Route/travel time. params: {origin, destination, ...}. Data includes live duration "
     "(with traffic) — use for 'when the commute drops below X minutes'."),
]


def _mcp_fetcher(agent_name: str, tool_name: str) -> Fetcher:
    """A params->dict fetcher that calls ``tool_name`` on ``agent_name``'s MCP server.

    The acting user/household is whatever is bound with ``using_mcp_principal`` (the turn
    when a watch is prepared; the watch owner when the scheduler polls it).

    Params come from an LLM-authored watch, so they may include plausible-but-unsupported
    keys; when the server's listing is known, keys outside the tool's input schema are
    dropped with a warning instead of failing every poll.
    """

    async def fetch(params: dict) -> dict:
        from lib.agents.mcp_subagents import toolset_for

        toolset = toolset_for(agent_name)
        if toolset is None:
            return {"Error": f"no tool server configured for {agent_name}"}
        await toolset.refresh()
        kwargs = dict(params or {})
        remote = {t.name: t for t in (toolset.remote_tools() or [])}
        schema = remote[tool_name].input_schema if tool_name in remote else None
        if schema and not schema.get("additionalProperties"):
            allowed = set((schema.get("properties") or {}).keys())
            unknown = [k for k in kwargs if k not in allowed]
            if unknown:
                logger.warning("watch source %s.%s: ignoring unsupported param(s) %s (tool accepts %s)",
                               agent_name, tool_name, unknown, sorted(allowed))
                kwargs = {k: v for k, v in kwargs.items() if k in allowed}
        text = await toolset.call(tool_name, kwargs)
        if text.startswith("ERROR"):
            return {"Error": text}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"Error": "tool returned non-JSON output"}
        return data if isinstance(data, dict) else {"result": data}

    return fetch


def _register_default_sources() -> None:
    """Register every watchable source from ``_WATCHABLE`` as an MCP-backed fetcher. A source
    whose server is down simply reads as 'unknown' when polled."""
    for source_id, agent_name, tool_name, hint in _WATCHABLE:
        register_source(source_id, _mcp_fetcher(agent_name, tool_name), hint)


_register_default_sources()
