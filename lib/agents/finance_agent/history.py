"""finance_agent private helpers: the daily-observations watchlist + historical/latest
fetchers that feed the time-series store.

Two data sources, both keyless (same ones the live tools use):
  * FX  — Frankfurter (``base=PLN``); we store PLN-per-unit-foreign for each watched pair.
  * Equities & metals — Yahoo Finance chart API (daily closes).

Series-key convention (stable across backfill and daily collection):
  * ``fx:USD/PLN``            — PLN per 1 USD (etc. for EUR/GBP/CHF)
  * ``index:^GSPC``           — S&P 500 index level (USD, price index — no dividends)
  * ``etf:amundi_sp500_eur``  — the owner's actual fund, the Amundi (ex-Lyxor) Core S&P 500
                                Swap UCITS ETF, DISTRIBUTING class (ISIN FR0011876061, GPW
                                ticker SP500), read from a EUR cross-listing Yahoo *does* carry
                                (default SP5.PA, Euronext Paris; LYPS.DE is its Xetra twin).
  * ``index:sp500_pln``       — that ETF expressed in PLN = (ETF price in EUR) × (PLN per EUR).
                                This is the real fund's PLN value (captures its TER; as a
                                distributing class its price is ex-dividends, matching what the
                                owner sees) — Yahoo has no GPW PLN listing for it (SP500.WA
                                absent) and stooq is behind an anti-bot wall, so the EUR listing
                                × EUR/PLN is the faithful keyless route. It MUST be the
                                distributing class — an accumulating line reinvests dividends
                                and would drift ~1.3%/yr above the owner's fund. Swap
                                ``OBSERVATIONS_SP500_ETF`` if a better listing appears.
  * ``metal:GC=F``            — gold future (USD/oz); ``metal:SI=F`` — silver future (USD/oz)

The watchlist is env-overridable (``OBSERVATIONS_FX`` / ``OBSERVATIONS_EQUITIES`` /
``OBSERVATIONS_METALS``, comma-separated) but ships with the owner's chosen set.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_HEADERS = {"User-Agent": "Jarvis (personal assistant)"}

SP500_PLN_SERIES = "index:sp500_pln"      # the real ETF, expressed in PLN (derived)
SP500_ETF_EUR_SERIES = "etf:amundi_sp500_eur"  # the real ETF, raw EUR price

_DEFAULT_FX = ["USD", "EUR", "GBP", "CHF"]
_DEFAULT_EQUITIES = ["^GSPC"]
_DEFAULT_METALS = ["GC=F", "SI=F"]
# Yahoo symbol of a EUR cross-listing of the owner's Amundi (ex-Lyxor) Core S&P 500 Swap
# UCITS ETF — the DISTRIBUTING class (ISIN FR0011876061, GPW ticker SP500). SP5.PA (Euronext
# Paris) is the default; LYPS.DE (Xetra) is the identical twin (same price, same dividends).
# Must be the *distributing* class to match the owner's fund: an accumulating line (e.g.
# A500.MI, 0 dividends/yr) reinvests dividends and would overstate the price by ~1.3%/yr.
# Its EUR price × EUR/PLN yields the fund's PLN value.
_DEFAULT_SP500_ETF = "SP5.PA"


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def fx_currencies() -> list[str]:
    """Foreign currencies tracked against PLN (env OBSERVATIONS_FX)."""
    return [c.upper() for c in _env_list("OBSERVATIONS_FX", _DEFAULT_FX)]


def equity_symbols() -> list[str]:
    """Yahoo tickers for indices/equities tracked (env OBSERVATIONS_EQUITIES)."""
    return _env_list("OBSERVATIONS_EQUITIES", _DEFAULT_EQUITIES)


def metal_symbols() -> list[str]:
    """Yahoo tickers for metals tracked, stored in USD (env OBSERVATIONS_METALS)."""
    return _env_list("OBSERVATIONS_METALS", _DEFAULT_METALS)


def sp500_etf_symbol() -> str:
    """Yahoo symbol of the EUR-listed Amundi S&P 500 ETF used to derive the PLN value
    (env OBSERVATIONS_SP500_ETF). Empty disables the ETF-based PLN series."""
    raw = os.getenv("OBSERVATIONS_SP500_ETF")
    return _DEFAULT_SP500_ETF if raw is None else raw.strip()  # unset -> default; "" -> disabled


def fx_series(currency: str) -> str:
    return f"fx:{currency.upper()}/PLN"


def equity_series(symbol: str) -> str:
    return f"index:{symbol}"


def metal_series(symbol: str) -> str:
    return f"metal:{symbol}"


def series_catalog() -> dict[str, str]:
    """Map of every series this watchlist produces -> a short human label (Polish). Used by
    the history tool to advertise what can be queried and to validate a requested series."""
    cat: dict[str, str] = {}
    for c in fx_currencies():
        cat[fx_series(c)] = f"Kurs {c}/PLN (PLN za 1 {c})"
    for s in equity_symbols():
        label = "Indeks S&P 500 (USD, indeks cenowy)" if s == "^GSPC" else f"Instrument {s}"
        cat[equity_series(s)] = label
    if sp500_etf_symbol():
        cat[SP500_ETF_EUR_SERIES] = f"Amundi S&P 500 UCITS ETF (kurs w EUR, {sp500_etf_symbol()})"
    if _sp500_pln_available():
        cat[SP500_PLN_SERIES] = "Amundi S&P 500 UCITS ETF w PLN (rzeczywisty ETF w EUR × EUR/PLN)"
    for s in metal_symbols():
        label = {"GC=F": "Złoto (USD/oz, futures)", "SI=F": "Srebro (USD/oz, futures)"}.get(s, f"Metal {s}")
        cat[metal_series(s)] = label
    return cat


def _sp500_pln_available() -> bool:
    """The derived PLN series needs the EUR-listed S&P 500 ETF and the EUR/PLN pair tracked."""
    return bool(sp500_etf_symbol()) and "EUR" in fx_currencies()


# Free-text aliases the coordinator/model is likely to pass, mapped to canonical series ids.
_STATIC_ALIASES = {
    "sp500": SP500_PLN_SERIES, "s&p500": SP500_PLN_SERIES, "s&p 500": SP500_PLN_SERIES,
    "sp500_pln": SP500_PLN_SERIES, "sp500pln": SP500_PLN_SERIES,
    "amundi": SP500_PLN_SERIES, "etf": SP500_PLN_SERIES, "amundi_eur": SP500_ETF_EUR_SERIES,
    "sp500_eur": SP500_ETF_EUR_SERIES,
    "^gspc": "index:^GSPC", "gspc": "index:^GSPC", "sp500_usd": "index:^GSPC",
    "gold": "metal:GC=F", "zloto": "metal:GC=F", "złoto": "metal:GC=F", "gc=f": "metal:GC=F",
    "silver": "metal:SI=F", "srebro": "metal:SI=F", "si=f": "metal:SI=F",
}


def resolve_series_alias(query: str) -> str | None:
    """Best-effort map free text ('USD', 'sp500', 'złoto', 'EUR/PLN') to a canonical series
    id present in the catalog. Returns None if nothing matches."""
    q = (query or "").strip()
    if not q:
        return None
    catalog = series_catalog()
    if q in catalog:  # already canonical
        return q
    low = q.lower()
    if low in _STATIC_ALIASES and _STATIC_ALIASES[low] in catalog:
        return _STATIC_ALIASES[low]
    # bare currency code -> fx pair
    if fx_series(q) in catalog:
        return fx_series(q)
    # "USD/PLN" style
    norm = low.replace(" ", "").replace("pln", "PLN").upper()
    if norm.endswith("/PLN") and f"fx:{norm}" in catalog:
        return f"fx:{norm}"
    return None


def summarize_series(rows: list[dict]) -> dict:
    """Compact, code-computed digest of a series (so the model gets trends, not a raw
    array to reason over): first/last value and date, min/max, mean, and % change."""
    values = [(r["date"], r["value"]) for r in rows if r.get("value") is not None]
    if not values:
        return {"count": 0}
    nums = [v for _d, v in values]
    first_d, first_v = values[0]
    last_d, last_v = values[-1]
    pct = round((last_v - first_v) / first_v * 100, 2) if first_v else None
    return {
        "count": len(values),
        "first": {"date": first_d, "value": first_v},
        "last": {"date": last_d, "value": last_v},
        "min": min(nums),
        "max": max(nums),
        "mean": round(sum(nums) / len(nums), 4),
        "change_pct": pct,
    }


# ---------------------------------------------------------------------------
# Fetchers — Frankfurter (FX) and Yahoo (equities/metals). Synchronous; the async
# collector wraps these in asyncio.to_thread, the backfill script calls them directly.
# ---------------------------------------------------------------------------

def fetch_fx_series(currencies: list[str], start: date, end: date) -> dict[str, dict[str, float]]:
    """Return ``{series_id -> {YYYY-MM-DD: PLN_per_unit}}`` for the given currencies over
    [start, end], from Frankfurter's time-series endpoint (``base=PLN``). Frankfurter yields
    foreign-per-PLN; we invert to PLN-per-foreign to match ``get_exchange_rate``'s sense."""
    if not currencies:
        return {}
    symbols = ",".join(currencies)
    url = f"{FRANKFURTER_BASE}/{start.isoformat()}..{end.isoformat()}?base=PLN&symbols={symbols}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    out: dict[str, dict[str, float]] = {fx_series(c): {} for c in currencies}
    for day, rates in (data.get("rates") or {}).items():
        for cur in currencies:
            rate = rates.get(cur)
            if rate:  # foreign per 1 PLN -> PLN per foreign
                out[fx_series(cur)][day] = round(1.0 / float(rate), 6)
    return out


def fetch_fx_latest(currencies: list[str]) -> dict[str, tuple[str, float]]:
    """Return ``{series_id -> (YYYY-MM-DD, PLN_per_unit)}`` for the latest available day."""
    if not currencies:
        return {}
    symbols = ",".join(currencies)
    resp = requests.get(f"{FRANKFURTER_BASE}/latest?base=PLN&symbols={symbols}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    day = data.get("date")
    out: dict[str, tuple[str, float]] = {}
    for cur in currencies:
        rate = (data.get("rates") or {}).get(cur)
        if rate and day:
            out[fx_series(cur)] = (day, round(1.0 / float(rate), 6))
    return out


def _yahoo_chart(symbol: str, params: dict) -> dict:
    resp = requests.get(f"{YAHOO_CHART}/{symbol}", headers=YAHOO_HEADERS, params=params, timeout=20)
    data = resp.json()
    chart = data.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"Yahoo returned no data for {symbol}: {(chart.get('error') or {})}")
    return chart["result"][0]


def fetch_yahoo_series(symbol: str, start: date, end: date) -> dict[str, float]:
    """Return ``{YYYY-MM-DD: close}`` daily closes for ``symbol`` over [start, end]."""
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
    result = _yahoo_chart(symbol, {"interval": "1d", "period1": period1, "period2": period2})
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    out: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        out[datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()] = round(float(close), 4)
    return out


def fetch_yahoo_latest(symbol: str) -> tuple[str, float, dict]:
    """Return ``(YYYY-MM-DD, price, meta)`` for the latest available close of ``symbol``."""
    result = _yahoo_chart(symbol, {"interval": "1d", "range": "5d"})
    meta = result.get("meta", {})
    # Prefer the settled last daily close; fall back to the live regularMarketPrice.
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    for ts, close in zip(reversed(timestamps), reversed(closes)):
        if close is not None:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), round(float(close), 4), meta
    price = meta.get("regularMarketPrice")
    qt = meta.get("regularMarketTime")
    day = datetime.fromtimestamp(qt, tz=timezone.utc).date().isoformat() if qt else date.today().isoformat()
    if price is None:
        raise ValueError(f"No usable price for {symbol}")
    return day, round(float(price), 4), meta


# ---------------------------------------------------------------------------
# Observation builders — used by both the backfill script and the daily collector.
# ---------------------------------------------------------------------------

def build_backfill_observations(days: int = 365) -> list[dict]:
    """Build one year (``days``) of daily observation rows for the whole watchlist,
    including the derived ``index:sp500_pln`` series. Returns rows ready for
    ``observations.record_many``. Raises on a hard source failure per symbol is avoided —
    a failing symbol is logged and skipped so one bad ticker doesn't sink the run."""
    end = date.today()
    start = end - timedelta(days=days)
    rows: list[dict] = []

    fx = {}
    try:
        fx = fetch_fx_series(fx_currencies(), start, end)
        for series, series_map in fx.items():
            for day, value in series_map.items():
                rows.append({"series": series, "date": day, "value": value, "source": "frankfurter"})
    except Exception:
        logger.exception("Backfill: FX fetch failed")

    for sym in equity_symbols():
        try:
            for day, value in fetch_yahoo_series(sym, start, end).items():
                rows.append({"series": equity_series(sym), "date": day, "value": value, "source": "yahoo"})
        except Exception:
            logger.exception("Backfill: Yahoo fetch failed for %s", sym)
    for sym in metal_symbols():
        try:
            for day, value in fetch_yahoo_series(sym, start, end).items():
                rows.append({"series": metal_series(sym), "date": day, "value": value,
                             "source": "yahoo", "payload": {"currency": "USD"}})
        except Exception:
            logger.exception("Backfill: Yahoo fetch failed for %s", sym)

    # The owner's actual Amundi S&P 500 ETF via its EUR cross-listing, plus the derived PLN
    # value (ETF in EUR × EUR/PLN), aligned by date on the days both inputs exist.
    etf_eur: dict[str, float] = {}
    etf_sym = sp500_etf_symbol()
    if etf_sym:
        try:
            etf_eur = fetch_yahoo_series(etf_sym, start, end)
            for day, price in etf_eur.items():
                rows.append({"series": SP500_ETF_EUR_SERIES, "date": day, "value": price,
                             "source": "yahoo", "payload": {"currency": "EUR", "symbol": etf_sym}})
        except Exception:
            logger.exception("Backfill: Yahoo fetch failed for S&P500 ETF %s", etf_sym)
    if _sp500_pln_available() and etf_eur:
        eurpln = fx.get(fx_series("EUR"), {})
        for day, price in etf_eur.items():
            fxv = eurpln.get(day)
            if fxv:
                rows.append({"series": SP500_PLN_SERIES, "date": day, "value": round(price * fxv, 4),
                             "source": "derived", "payload": {"formula": f"{etf_sym}(EUR)*EUR/PLN"}})
    return rows


def build_daily_observations() -> list[dict]:
    """Build today's observation rows for the whole watchlist (latest available close per
    series), including the derived PLN S&P 500. Each row is keyed by the *source's* quote
    date, so weekends/holidays never fabricate a row and a re-run upserts the same day."""
    rows: list[dict] = []

    fx_latest = {}
    try:
        fx_latest = fetch_fx_latest(fx_currencies())
        for series, (day, value) in fx_latest.items():
            rows.append({"series": series, "date": day, "value": value, "source": "frankfurter"})
    except Exception:
        logger.exception("Daily collect: FX fetch failed")

    for sym in equity_symbols():
        try:
            day, value, _meta = fetch_yahoo_latest(sym)
            rows.append({"series": equity_series(sym), "date": day, "value": value, "source": "yahoo"})
        except Exception:
            logger.exception("Daily collect: Yahoo fetch failed for %s", sym)
    for sym in metal_symbols():
        try:
            day, value, _meta = fetch_yahoo_latest(sym)
            rows.append({"series": metal_series(sym), "date": day, "value": value,
                         "source": "yahoo", "payload": {"currency": "USD"}})
        except Exception:
            logger.exception("Daily collect: Yahoo fetch failed for %s", sym)

    etf_latest = None
    etf_sym = sp500_etf_symbol()
    if etf_sym:
        try:
            day, price, _meta = fetch_yahoo_latest(etf_sym)
            rows.append({"series": SP500_ETF_EUR_SERIES, "date": day, "value": price,
                         "source": "yahoo", "payload": {"currency": "EUR", "symbol": etf_sym}})
            etf_latest = (day, price)
        except Exception:
            logger.exception("Daily collect: Yahoo fetch failed for S&P500 ETF %s", etf_sym)

    if _sp500_pln_available() and etf_latest is not None:
        eur = fx_latest.get(fx_series("EUR"))
        if eur:
            day, price = etf_latest
            rows.append({"series": SP500_PLN_SERIES, "date": day, "value": round(price * eur[1], 4),
                         "source": "derived", "payload": {"formula": f"{etf_sym}(EUR)*EUR/PLN"}})
    return rows
