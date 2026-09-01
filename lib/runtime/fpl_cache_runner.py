"""FPL league-squad cache refresher: an out-of-band loop that keeps the owner's default
mini-league warm in Postgres so the ownership questions asked most often ("who else has
player X", "how many own a player from team Y") are served from the database instead of a
dozens-of-calls live API scan.

It refreshes only ``FPL_LEAGUE_ID`` — the owner's configured private league — deliberately
never national/global leagues (those have thousands/millions of managers and cannot be
cached). Other private leagues the owner asks about are still cached lazily on first use by
``load_league_squads``'s read-through path; only the *scheduled* warming is scoped here.

Runs in-process inside the long-running webhook service (started from ``app/webhook.py``
``lifespan``), like the scheduler loop. Everything is best-effort: a failed refresh is
logged and swallowed so the loop never dies and never takes the service down.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 10800  # 3 hours


def fpl_cache_refresh_enabled() -> bool:
    """Master switch (env FPL_CACHE_REFRESH_ENABLED); enabled unless set to a falsy value.
    Same falsy-parsing as the scheduler's ``scheduler_enabled``."""
    raw = os.getenv("FPL_CACHE_REFRESH_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def refresh_interval_seconds() -> int:
    """How often the loop refreshes the owner's league (env
    FPL_CACHE_REFRESH_INTERVAL_SECONDS; default 3 hours)."""
    try:
        return max(60, int(os.getenv("FPL_CACHE_REFRESH_INTERVAL_SECONDS") or _DEFAULT_INTERVAL))
    except ValueError:
        return _DEFAULT_INTERVAL


def _configured_league_id() -> Optional[int]:
    raw = os.getenv("FPL_LEAGUE_ID")
    return int(raw) if raw and raw.strip().isdigit() else None


async def refresh_once() -> bool:
    """Force-refresh the owner's configured league squads for the current gameweek.

    Returns True when a refresh ran, False when it was skipped (no league configured, no
    resolvable gameweek). Best-effort: exceptions are logged and swallowed (returns False)."""
    from lib.agents.fpl_agent.fpl_api import (
        fetch_fpl_bootstrap,
        resolve_gameweek,
        load_league_squads,
        _LEAGUE_SCAN_DEFAULT,
    )

    lid = _configured_league_id()
    if lid is None:
        logger.debug("FPL cache refresh skipped: FPL_LEAGUE_ID not configured")
        return False
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, None, prefer="current")
        if gw is None:
            logger.debug("FPL cache refresh skipped: no current gameweek")
            return False
        await load_league_squads(lid, gw, _LEAGUE_SCAN_DEFAULT, force=True)
        logger.info("FPL league %s GW%s squads cache warmed", lid, gw)
        return True
    except Exception:
        logger.exception("FPL cache refresh failed for league %s", lid)
        return False


async def run_fpl_cache_loop(seconds: Optional[int] = None) -> None:
    """Warm the owner's league-squad cache immediately, then every ``seconds``. Never raises
    (other than on cancellation at shutdown)."""
    seconds = seconds or refresh_interval_seconds()
    logger.info("FPL cache refresh loop started (interval=%ss)", seconds)
    while True:
        try:
            await refresh_once()
        except asyncio.CancelledError:
            logger.info("FPL cache refresh loop cancelled; stopping")
            raise
        except Exception:
            logger.exception("FPL cache refresh tick failed")
        await asyncio.sleep(seconds)
