"""One-shot backfill of the FPL mini-league history (the `fpl_league_history` table).

Reconstructs one snapshot per PAST gameweek — every manager's rank, points and squad — from
`from_gw` (default 1) up to the current gameweek, so the league-history tool has depth
immediately instead of only accumulating from the next 04:00 collection. Each snapshot is
dated at that gameweek's deadline; in-league rank per gameweek is reconstructed from
cumulative points. Idempotent (upsert per league+date). Postgres required.

Usage:
    poetry run python -m scripts.backfill_fpl_history                 # league from FPL_LEAGUE_ID, GW1..now
    poetry run python -m scripts.backfill_fpl_history --from-gw 5
    poetry run python -m scripts.backfill_fpl_history --league 11289
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

from dotenv import load_dotenv


async def _run(league_id: int, from_gw: int) -> int:
    from lib.agents.fpl_agent.fpl_api import backfill_league_history
    result = await backfill_league_history(league_id, from_gw=from_gw)
    if result.get("error"):
        print(f"error: {result['error']}")
        return 1
    print(f"League {result['league_id']} — {result.get('league_name')}")
    print(f"Backfilled {len(result['gameweeks'])} gameweek snapshot(s):")
    for gw in result["gameweeks"]:
        print(f"  GW{gw['gameweek']:<2} {gw['date']}  {gw['managers']} managers")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill FPL mini-league history per gameweek.")
    parser.add_argument("--league", type=int, default=None, help="League id (default: FPL_LEAGUE_ID).")
    parser.add_argument("--from-gw", type=int, default=1, help="First gameweek to backfill (default 1).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    from app.db import connection as dbconn
    if not dbconn.is_configured():
        print("error: DATABASE_URL is not set (FPL history is Postgres-only).")
        return 2

    league_id = args.league
    if league_id is None:
        raw = os.getenv("FPL_LEAGUE_ID")
        if not (raw and raw.strip().isdigit()):
            print("error: no league id (pass --league or set FPL_LEAGUE_ID).")
            return 2
        league_id = int(raw)

    return asyncio.run(_run(league_id, args.from_gw))


if __name__ == "__main__":
    raise SystemExit(main())
