"""One-shot backfill of the finance time-series (the `observations` store).

Fetches ~one year of DAILY history for the whole watchlist — FX pairs vs PLN (Frankfurter),
the S&P 500 index and its PLN stand-in, and metals in USD (Yahoo) — and upserts it, so the
history tools have depth from day one instead of only accumulating going forward.

Idempotent: every row is an upsert keyed by (series, date), so re-running is safe and simply
refreshes existing days. Writes to Postgres when DATABASE_URL is set, otherwise to the
on-disk JSON store under data/observations_data/ (see lib/domain/observations.py).

Usage:
    poetry run python -m scripts.backfill_finance            # default: 365 days
    poetry run python -m scripts.backfill_finance --days 730
"""
from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the finance observations time-series.")
    parser.add_argument("--days", type=int, default=365, help="How many days back to fetch (default 365).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    # Imported after load_dotenv so the store picks the right backend from DATABASE_URL.
    from lib.agents.finance_agent import history
    from lib.domain import observations
    from app.db import connection as dbconn

    backend = "Postgres" if dbconn.is_configured() else "JSON file (data/observations_data/)"
    print(f"Backfilling {args.days} day(s) of finance observations into: {backend}")
    print(f"Watchlist: FX={history.fx_currencies()}  equities={history.equity_symbols()}  metals={history.metal_symbols()}")

    rows = history.build_backfill_observations(days=args.days)
    if not rows:
        print("No rows fetched (check network / watchlist). Nothing written.")
        return 1

    written = observations.record_many(rows)

    # Per-series summary of what landed.
    by_series: dict[str, list[str]] = {}
    for r in rows:
        by_series.setdefault(r["series"], []).append(str(r["date"]))
    print(f"\nWrote {written} row(s) across {len(by_series)} series:")
    for series in sorted(by_series):
        dates = sorted(by_series[series])
        print(f"  {series:<22} {len(dates):>4} days  [{dates[0]} … {dates[-1]}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
