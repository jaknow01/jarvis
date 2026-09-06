"""Daily observation collectors: out-of-band loops that populate the time-series store so
the assistant can analyze data over time, not just report the current snapshot.

Two deterministic (no-LLM) collectors, each fired once per day at a configured local hour:

  * FINANCE  — 23:00 (env OBSERVATIONS_FINANCE_HOUR): the FX/index/metals watchlist, upserted
    into the `observations` store (see lib/agents/finance_agent/history.py).
  * FPL      — 04:00 (env OBSERVATIONS_FPL_HOUR): a permanent daily snapshot of the owner's
    mini-league (every manager's rank, points and squad) into `fpl_league_history`.

Runs in-process inside the long-running webhook service (started from app/webhook.py
`lifespan`), like the scheduler and FPL-cache loops. Time-of-day is evaluated in the
scheduler timezone (Europe/Warsaw by default, via lib.domain.now_tz). Firing is guarded by
an in-memory "last run date" per collector; because every write is an idempotent upsert
keyed by date, a restart re-running within the same hour is harmless. Everything is
best-effort: a failed collection is logged and swallowed so the loop never dies.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_TICK = 300  # seconds
_DEFAULT_FINANCE_HOUR = 23
_DEFAULT_FPL_HOUR = 4


def observations_enabled() -> bool:
    """Master switch (env OBSERVATIONS_ENABLED); enabled unless set to a falsy value.
    Same falsy-parsing as the scheduler's ``scheduler_enabled``."""
    raw = os.getenv("OBSERVATIONS_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _int_env(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def tick_seconds() -> int:
    return max(30, _int_env("OBSERVATIONS_TICK_SECONDS", _DEFAULT_TICK))


def finance_hour() -> int:
    return _int_env("OBSERVATIONS_FINANCE_HOUR", _DEFAULT_FINANCE_HOUR) % 24


def fpl_hour() -> int:
    return _int_env("OBSERVATIONS_FPL_HOUR", _DEFAULT_FPL_HOUR) % 24


def _configured_league_id() -> Optional[int]:
    raw = os.getenv("FPL_LEAGUE_ID")
    return int(raw) if raw and raw.strip().isdigit() else None


async def collect_finance_once() -> int:
    """Fetch the current watchlist values and upsert them into the observations store.
    Returns the number of rows written. Best-effort: exceptions are logged and swallowed."""
    from lib.agents.finance_agent import history
    from lib.domain import observations
    try:
        rows = await asyncio.to_thread(history.build_daily_observations)
        if not rows:
            logger.warning("Finance collect produced no rows")
            return 0
        written = await asyncio.to_thread(observations.record_many, rows)
        logger.info("Finance observations collected: %d series rows", written)
        return written
    except Exception:
        logger.exception("Finance observation collection failed")
        return 0


async def collect_fpl_once() -> bool:
    """Record today's league-history snapshot for the owner's configured league.
    Returns True when a snapshot was saved. Best-effort."""
    from lib.agents.fpl_agent.fpl_api import collect_league_history
    lid = _configured_league_id()
    if lid is None:
        logger.info("FPL history collect skipped: FPL_LEAGUE_ID not configured")
        return False
    ok = await collect_league_history(lid)
    if ok:
        logger.info("FPL league %s history snapshot collected", lid)
    return ok


async def run_observations_loop(seconds: Optional[int] = None) -> None:
    """Fire the finance and FPL collectors once each per day at their configured local hours.
    Never raises (other than on cancellation at shutdown)."""
    from lib.domain import now_tz

    seconds = seconds or tick_seconds()
    fin_hour, fpl_h = finance_hour(), fpl_hour()
    logger.info(
        "Observations loop started (tick=%ss, finance@%02d:00, fpl@%02d:00)",
        seconds, fin_hour, fpl_h,
    )
    last_finance: Optional[date] = None
    last_fpl: Optional[date] = None
    while True:
        try:
            now = now_tz()
            today = now.date()
            if now.hour == fin_hour and last_finance != today:
                last_finance = today
                await collect_finance_once()
            if now.hour == fpl_h and last_fpl != today:
                last_fpl = today
                await collect_fpl_once()
        except asyncio.CancelledError:
            logger.info("Observations loop cancelled; stopping")
            raise
        except Exception:
            logger.exception("Observations loop tick failed")
        await asyncio.sleep(seconds)
