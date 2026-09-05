# Observations — daily time-series store

Jarvis's regular briefs used to show finance/FPL data only as **snapshots** — the value at
one moment, with no sense of how it changed. The **observations** layer records selected
readings **once a day** so agents can answer change-over-time questions ("how much did gold
rise this month", "who climbed my FPL table", "what did USD/PLN do over the year") — not
just "what is it now".

## What is collected

Two deterministic (no-LLM) collectors run in the long-running service
(`lib/runtime/observations_runner.py`, started from the webhook `lifespan`), each once per
day at a configured local hour (timezone = `SCHEDULER_TIMEZONE`, default Europe/Warsaw):

| Collector | Time | What it stores | Where |
|-----------|------|----------------|-------|
| **Finance** | 23:00 | The FX/index/metals watchlist (one scalar per series per day) | `observations` table |
| **FPL** | 04:00 | A snapshot of the owner's mini-league — every manager's rank, points **and** squad | `fpl_league_history` table |

Firing is guarded by an in-memory "last run date" per collector; every write is an
**idempotent upsert keyed by date**, so a restart re-running within the same hour is
harmless. Everything is best-effort — a failed collection is logged and swallowed so the
loop never dies.

### Finance watchlist

Env-overridable (`OBSERVATIONS_FX` / `OBSERVATIONS_EQUITIES` / `OBSERVATIONS_METALS`),
default = the owner's chosen set. Sources are the same keyless ones the live tools use:

| Series id | Meaning | Source |
|-----------|---------|--------|
| `fx:USD/PLN`, `fx:EUR/PLN`, `fx:GBP/PLN`, `fx:CHF/PLN` | PLN per 1 unit of the foreign currency | Frankfurter |
| `index:^GSPC` | S&P 500 index level (USD, price index) | Yahoo |
| `etf:amundi_sp500_eur` | The owner's Amundi S&P 500 ETF, raw EUR price | Yahoo (`SP5.PA`) |
| `index:sp500_pln` | That ETF in PLN = `ETF(EUR) × EUR/PLN` | derived |
| `metal:GC=F`, `metal:SI=F` | Gold / silver future (USD/oz) | Yahoo |

**How the PLN S&P 500 is obtained.** The owner holds the *Amundi (ex-Lyxor) Core S&P 500 Swap
UCITS ETF*, **distributing** class (ISIN FR0011876061, GPW ticker `SP500`), quoted in PLN on
GPW. No keyless source exposes that GPW PLN quote — Yahoo has no GPW listing (`SP500.WA`
absent) and stooq is behind an anti-bot proof-of-work we do not bypass. Yahoo **does** carry
the same fund's EUR cross-listings — `SP5.PA` (Euronext Paris) and its Xetra twin `LYPS.DE`,
both distributing (one ~0.60 EUR dividend/yr, confirmed via Yahoo's dividend events) — so we
take the real ETF's EUR price and convert it with the EUR/PLN we already track:
`index:sp500_pln = ETF(EUR) × EUR/PLN`. Using the actual **distributing** class matters: its
price is ex-dividends, matching what the owner sees on GPW, whereas an accumulating line
(e.g. `A500.MI`/`AUM5.DE`, 0 dividends/yr) reinvests dividends and would drift ~1.3%/yr above
the owner's fund. Set `OBSERVATIONS_SP500_ETF` to a different Yahoo symbol (or empty to
disable) if a better listing appears; the series id and everything downstream stay the same.

## Backfill

Daily collection only builds history going forward. To get depth immediately, backfill one
year of daily finance history (idempotent — safe to re-run):

```bash
poetry run python -m scripts.backfill_finance          # 365 days
poetry run python -m scripts.backfill_finance --days 730
```

FX comes from Frankfurter's time-series endpoint, equities/metals from Yahoo's chart range;
the derived PLN S&P 500 is computed per day where both inputs exist.

FPL history can also be backfilled per past gameweek (Postgres required):

```bash
poetry run python -m scripts.backfill_fpl_history            # league from FPL_LEAGUE_ID, GW1..now
poetry run python -m scripts.backfill_fpl_history --from-gw 5
```

For each past gameweek it reconstructs every manager's squad (`entry/{id}/event/{g}/picks/`)
and points (`entry/{id}/history/`), and rebuilds the in-league rank per gameweek by ordering
managers on cumulative points; each snapshot is dated at that gameweek's deadline.

Both collectors can also be triggered on demand from the admin surface (owner-only):
`/collect` runs both, `/collect finance` / `/collect fpl` just one.

## Reading it back

The history tools live **inside the domain agents** (each agent fetches its own history),
returning code-computed summaries rather than raw arrays so the model gets the trend, not a
token-heavy list to reason over:

- **`finance_agent.get_price_history(instrument, period_days=90)`** — reads a series from the
  `observations` store. Accepts canonical ids or free text (`'USD'`, `'sp500'`, `'złoto'`,
  `'^GSPC'`). Returns a summary (first/last/min/max/mean/% change) plus the daily points
  (downsampled if very long).
- **`fpl_agent.get_league_history(view, days=30, manager=None)`** — reads
  `fpl_league_history`. `view="standings"` gives rank/points movement (league-wide deltas, or
  one manager's daily series); `view="squads"` gives which footballers a manager had per
  gameweek (or the latest snapshot for the whole league).

For arbitrary arithmetic over the numbers these return (averages, min/max, trend,
correlation), the coordinator can delegate to the **`math_agent`** — a general, deterministic
calculator that is independent of this store.

## Storage

Postgres when `DATABASE_URL` is set (tables created idempotently by `app/db/schema.py` via
`init_db()`; repos in `app/db/observations_repo.py` and `app/db/fpl_history_repo.py`),
otherwise the finance observations fall back to `data/observations_data/observations.json`.
FPL history is **Postgres-only** (no live-API fallback — it *is* the accumulated record);
without a database the FPL collector logs and skips, and `get_league_history` reports that no
history is available.

## Configuration summary

See `.env_template`. Key vars: `OBSERVATIONS_ENABLED`, `OBSERVATIONS_FINANCE_HOUR` (23),
`OBSERVATIONS_FPL_HOUR` (4), `OBSERVATIONS_TICK_SECONDS` (300), and the watchlist
(`OBSERVATIONS_FX` / `OBSERVATIONS_EQUITIES` / `OBSERVATIONS_METALS`). The FPL collector uses
the existing `FPL_LEAGUE_ID`.
