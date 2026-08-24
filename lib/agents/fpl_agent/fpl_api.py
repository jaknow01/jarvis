"""fpl_agent private helpers: keyless FPL API fetch + bootstrap reference-data caching
and id->name indexing. Moved here from the former shared lib/tools_utils — all remaining
helpers had fpl_agent as their single consumer."""

import asyncio
import os
import time
import requests
from typing import Literal, Optional, Union
import logging

logger = logging.getLogger(__name__)



# ------- Fantasy Premier League (unofficial API) helpers -------
#
# The public FPL API (https://fantasy.premierleague.com/api/) is keyless and returns
# everything referenced by numeric ids (teams, players/"elements", positions,
# gameweeks/"events"). These helpers fetch the reference data once and expose plain
# lookup dicts so the tools can translate ids -> human-readable names on the server
# side, in the same spirit as the devices/maps tools: the model works with names, not
# raw numeric handles.

FPL_BASE = "https://fantasy.premierleague.com/api"
# The API blocks requests without a browser-like User-Agent, so send one.
FPL_HEADERS = {"User-Agent": "Mozilla/5.0 (Jarvis personal assistant)"}

# bootstrap-static is large (~700 players) and only changes ~daily (prices/form/news).
# It is cached on two levels: an in-process L1 memo with a short TTL (avoids a DB round
# trip within a single turn) and, when a database is configured, a persistent Postgres
# cache shared across processes/restarts (the "our database" store the owner asked for).
# On a miss at both levels we hit the API and write through to both caches.
_FPL_BOOTSTRAP_CACHE: dict = {"data": None, "fetched_at": 0.0}
_FPL_L1_TTL = 300  # seconds; in-process memo


def _fpl_db_ttl() -> int:
    """Max age (seconds) the Postgres bootstrap cache may reach before it is refreshed
    from the API. Configurable via FPL_CACHE_TTL_SECONDS; defaults to 6 hours."""
    raw = os.getenv("FPL_CACHE_TTL_SECONDS")
    try:
        return int(raw) if raw and raw.strip() else 21600
    except ValueError:
        return 21600


async def fetch_fpl(path: str) -> Union[dict, list]:
    """GET a JSON document from the FPL API. `path` is relative to FPL_BASE
    (e.g. 'fixtures/?event=2'). Runs the blocking request off the event loop so
    concurrent FPL tool calls don't serialize. Raises on HTTP errors."""
    url = f"{FPL_BASE}/{path.lstrip('/')}"

    def _do():
        resp = requests.get(url, headers=FPL_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()

    return await asyncio.to_thread(_do)


def _load_bootstrap_from_db(max_age: int) -> Optional[dict]:
    """Read a fresh-enough bootstrap snapshot from Postgres, or None. Best-effort:
    any error (no DB configured, connection/schema issue) falls back to the API."""
    try:
        from app.db import connection as dbconn
        if not dbconn.is_configured():
            return None
        from app.db.fpl_repo import get_cached_bootstrap
        return get_cached_bootstrap(max_age)
    except Exception as e:  # pragma: no cover - defensive; DB is optional
        logger.warning("FPL bootstrap DB cache read failed (%s); using API", e)
        return None


def _save_bootstrap_to_db(data: dict) -> None:
    """Write the fresh bootstrap snapshot to Postgres. Best-effort (optional DB)."""
    try:
        from app.db import connection as dbconn
        if not dbconn.is_configured():
            return
        from app.db.fpl_repo import save_bootstrap
        save_bootstrap(data)
    except Exception as e:  # pragma: no cover - defensive; DB is optional
        logger.warning("FPL bootstrap DB cache write failed (%s)", e)


async def fetch_fpl_bootstrap(force: bool = False) -> dict:
    """Fetch (and cache) the bootstrap-static reference document: teams, players
    ('elements'), positions ('element_types') and gameweeks ('events').

    Resolution order: in-process L1 memo -> Postgres cache (if configured & fresh) ->
    live API (written through to both caches). `force=True` bypasses all caches."""
    now = time.time()
    cache = _FPL_BOOTSTRAP_CACHE
    if not force and cache["data"] is not None and now - cache["fetched_at"] < _FPL_L1_TTL:
        return cache["data"]

    if not force:
        cached = await asyncio.to_thread(_load_bootstrap_from_db, _fpl_db_ttl())
        if cached is not None:
            cache["data"], cache["fetched_at"] = cached, now
            return cached

    data = await fetch_fpl("bootstrap-static/")
    cache["data"], cache["fetched_at"] = data, now
    await asyncio.to_thread(_save_bootstrap_to_db, data)
    return data


def index_bootstrap(bootstrap: dict) -> tuple[dict, dict, dict]:
    """Return (teams_by_id, elements_by_id, positions_by_id) lookups keyed by the
    numeric ids used throughout the FPL API."""
    teams = {t["id"]: t for t in bootstrap.get("teams", [])}
    elements = {e["id"]: e for e in bootstrap.get("elements", [])}
    positions = {p["id"]: p for p in bootstrap.get("element_types", [])}
    return teams, elements, positions


def resolve_gameweek(bootstrap: dict, gameweek: Optional[int], prefer: Literal["current", "next"] = "current") -> Optional[int]:
    """Resolve a concrete gameweek id. If `gameweek` is given, return it as-is.
    Otherwise fall back to the current (or next) gameweek from the events list.
    Returns None only if the events list is empty."""
    if gameweek is not None:
        return int(gameweek)
    events = bootstrap.get("events", [])
    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)
    chosen = (current or nxt) if prefer == "current" else (nxt or current)
    return chosen["id"] if chosen else None


def relevant_gameweek(bootstrap: dict) -> Optional[int]:
    """The gameweek whose matches are 'in play' for questions like "today's / the
    upcoming matches". While the current gameweek is still running (is_current and not
    finished) its round is what "today" and "the nearest matches" belong to, so return
    it; once it has finished, the round in play is the next gameweek. Falls back to the
    current gameweek if there is no next one (end of season)."""
    events = bootstrap.get("events", [])
    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)
    if current and not current.get("finished"):
        return current["id"]
    if nxt:
        return nxt["id"]
    return current["id"] if current else None


# Fixture "stats" identifiers we surface as match events, mapped to friendlier keys.
_FIXTURE_EVENT_IDENTIFIERS = {
    "goals_scored": "goals",
    "assists": "assists",
    "own_goals": "own_goals",
    "yellow_cards": "yellow_cards",
    "red_cards": "red_cards",
    "penalties_missed": "penalties_missed",
    "penalties_saved": "penalties_saved",
}


def summarize_fixture_events(fixture: dict, elements_by_id: dict, teams_by_id: dict) -> dict:
    """Turn a fixture's raw `stats` array into name-based match events (who scored,
    assisted, got booked, etc.). Each event lists the player's web name and team short
    code, resolved from the numeric element/team ids."""
    home = teams_by_id.get(fixture.get("team_h"), {})
    away = teams_by_id.get(fixture.get("team_a"), {})
    events: dict = {}
    for stat in fixture.get("stats", []):
        key = _FIXTURE_EVENT_IDENTIFIERS.get(stat.get("identifier"))
        if not key:
            continue
        entries = []
        for side, team in (("h", home), ("a", away)):
            for item in stat.get(side, []):
                element = elements_by_id.get(item.get("element"), {})
                entries.append({
                    "player": element.get("web_name"),
                    "team": team.get("short_name"),
                    "count": item.get("value"),
                })
        if entries:
            events[key] = entries
    return events


def find_players_by_name(bootstrap: dict, query: str) -> list[dict]:
    """Resolve a free-text player name (e.g. 'Haaland', 'Dalot', 'Bruno Fernandes')
    to matching element records, best match first. An exact web-name match wins and is
    returned alone; otherwise all substring matches (across web/first/second name) are
    returned so the caller can disambiguate."""
    q = (query or "").strip().lower()
    if not q:
        return []
    exact, partial = [], []
    for e in bootstrap.get("elements", []):
        web = (e.get("web_name") or "").lower()
        haystack = " ".join([web, (e.get("first_name") or ""), (e.get("second_name") or "")]).lower()
        if web == q:
            exact.append(e)
        elif q in haystack:
            partial.append(e)
    return exact or partial


def describe_player(element: dict, teams_by_id: dict, positions_by_id: dict) -> dict:
    """Flatten one 'element' (player) record into a compact, name-based summary."""
    team = teams_by_id.get(element.get("team"), {})
    position = positions_by_id.get(element.get("element_type"), {})
    return {
        "name": element.get("web_name"),
        "full_name": f"{element.get('first_name', '')} {element.get('second_name', '')}".strip(),
        "team": team.get("name"),
        "team_short": team.get("short_name"),
        "position": position.get("singular_name_short"),
        "price": round(element.get("now_cost", 0) / 10, 1),  # FPL prices are in tenths of £m
        "total_points": element.get("total_points"),
        "form": element.get("form"),
        "selected_by_percent": element.get("selected_by_percent"),
        # status 'a'=available, 'i'=injured, 'd'=doubtful, 's'=suspended, 'u'=unavailable
        "status": element.get("status"),
        "news": element.get("news") or None,
    }
