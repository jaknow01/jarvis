"""Named data-source registry for conditional watches (three-state reads).

Maps a ``source-id`` to a deterministic fetcher returning a ``dict``. A watch source is
just one of the assistant's **read-only external-API tools** reused as a pollable data
feed — **no LLM/agent run is involved**, only the tool's underlying coroutine. The catalog
is therefore intended to be *as broad as the assistant's data tools*: adding a new source
is one row in ``_WATCHABLE`` (a read-only tool → a source-id + a short predicate-writing
hint), not a bespoke fetcher. Mutating tools (smart-home on/off, memory writes, the
scheduler itself) are deliberately **not** exposed — a watch polls, it never acts.

``fetch_source`` wraps a fetch into a three-state result:

    ("ok", data)        - a clean data dict
    ("unknown", None)   - unknown source, the fetch raised, or it returned an error dict
                          (e.g. {"Error": ...}); never mistaken for a `false` predicate

The ``ctx`` argument of the underlying tools is decorative (``app_context`` reads a
contextvar, not the argument), so tools are called with ``ctx=None``.

This module lives in the ``agents`` layer (alongside the ``scheduler_agent`` that owns
watches) because it reuses other agents' data fetchers — an agents->agents import — and is
consumed both by the watch tools (agents) and the scheduler runner (runtime). The catalog
of valid source-ids is **owned here** (dynamic, derived from the registered tools) and is
the single source of truth for source validation and for what the model is told exists.
"""
from __future__ import annotations

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


def _resolve_tool(agent_name: str, tool_name: str):
    """Find a registered FunctionTool by name in an agent's tool list."""
    from lib.core import TOOLS_BY_AGENT

    for t in TOOLS_BY_AGENT.get(agent_name, []):
        if t.metadata.name == tool_name:
            return t
    raise KeyError(f"{agent_name}.{tool_name}")


def _tool_fetcher(tool) -> Fetcher:
    """Wrap a tool's coroutine as a params->dict fetcher (ctx is decorative -> None)."""
    async def fetch(params: dict) -> dict:
        return await tool.async_fn(None, **(params or {}))
    return fetch


def _register_default_sources() -> None:
    """Register every watchable source from ``_WATCHABLE``.

    The underlying agent packages must be imported so their tools are in TOOLS_BY_AGENT;
    we import them explicitly here (registration side effect) rather than relying on the
    order lib.agents wires subpackages, so this works no matter who imports us first.
    """
    import lib.agents.finance_agent  # noqa: F401
    import lib.agents.weather_agent  # noqa: F401
    import lib.agents.fpl_agent      # noqa: F401
    import lib.agents.news_agent     # noqa: F401
    import lib.agents.maps_agent     # noqa: F401

    for source_id, agent_name, tool_name, hint in _WATCHABLE:
        try:
            tool = _resolve_tool(agent_name, tool_name)
        except KeyError:
            logger.error(
                "watchable tool %s.%s not found; watch source '%s' skipped",
                agent_name, tool_name, source_id,
            )
            continue
        register_source(source_id, _tool_fetcher(tool), hint)


_register_default_sources()
