"""finance_agent tools: FX rates (Frankfurter) and stock/index quotes (Yahoo Finance).
Registered into TOOLS_BY_AGENT["finance_agent"] via @tool_ownership."""

import asyncio
import logging
from datetime import datetime, date, timedelta

import requests
from requests import HTTPError
from llama_index.core.workflow import Context

from lib.core import tool_ownership, function_tool, app_context
from lib.agents.finance_agent.fx import validate_currency_code
from lib.agents.finance_agent import history as _history
from lib.domain import observations as _observations

logger = logging.getLogger(__name__)


@tool_ownership("finance_agent")
@function_tool
async def get_exchange_rate(ctx: Context,
                            foreign_currency: str,
                            base_currency: str = "PLN") -> dict:
    f"""
    Description:
        This tool is used to obtain the current exchange rate between a given foreign and
        the base currency.

    Parameters:
    ctx : Context
        Context in which the tool operates

    foreign_currency: str
        Currency code of the currency that is to be checked against the base currency.
        Important: currency code **must** be a 3-letter code that is compatible with
        ISO 4217 standard e.g. us dollar -> USD, euro -> EUR etc.

    base_currency: str
        Currency code of the base currency in the exchange rate. Unless specified clearly
        in the user's query this should always remain "PLN" by default.

    Output:
        JSON object with the current exchange rate of the foreign_currency and base currency
    """

    logging.info(f"Getting exchange data for {base_currency} and {foreign_currency}")

    if len(base_currency)>3 or len(foreign_currency)>3:
        return {
            "Error" : f"Currency codes must always have exactly 3 letters. One of these codes {base_currency}, {foreign_currency} is incorrect.",
            "Tip": "You should rerun this tool with correct currency codes."
        }

    base_currency = base_currency.upper()
    foreign_currency = foreign_currency.upper()
    is_base_valid = validate_currency_code(base_currency)
    is_foreign_valid = validate_currency_code(foreign_currency)

    if not is_base_valid or not is_foreign_valid:
        return {
            "Error" : f"{base_currency if not is_base_valid else foreign_currency} is not a valid currency code."
        }

    def _fetch() -> dict:
        resp = requests.get(
            f"https://api.frankfurter.dev/v1/latest?base={base_currency}&to={foreign_currency}",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    try:
        # Blocking HTTP runs off the event loop so it can't stall the other agents/tools.
        data_json = await asyncio.to_thread(_fetch)

        base = float(data_json["amount"])
        rate = float(data_json["rates"][foreign_currency])

        exchange_rate = base/rate

    except Exception as e:
        logging.warning(f"Frankfurter request failed [base: {base_currency}, to: {foreign_currency}]: {e}")
        return {
            "message" : "Could not get the exchange rate from the Frankfurter API",
            "error" : str(e),
            "tip" : "Check that both codes are valid ISO 4217 currencies supported by the ECB "
                    "reference rates. If they are, the source is temporarily unavailable — "
                    "report that instead of retrying repeatedly.",
        }

    return {
        "message" : f"{base_currency}/{foreign_currency} exchange rate is {exchange_rate}"
    }

@tool_ownership("finance_agent")
@function_tool
async def get_stock_quote(ctx: Context, symbol: str) -> dict:
    """
    Description:
        This tool returns the latest market quote (current price, day OHLC, previous
        close and volume) for a stock, ETF or index, using the free Yahoo Finance
        data source. It covers the Polish stock market (GPW) as well as global markets.

    Parameters:
    ctx : Context
        Context in which the tool operates

    symbol: str
        The Yahoo Finance ticker of the instrument.
        - Polish (GPW) stocks: the ticker with a '.WA' suffix, e.g. 'PKO.WA',
          'KGH.WA', 'CDR.WA', 'PKN.WA'.
        - Non-Polish stocks: the plain ticker, e.g. 'AAPL', 'MSFT'.
        - Indices: prefix with '^', e.g. '^GSPC' (S&P 500), '^WIG20' (WIG20).
        The symbol is case-insensitive.

    Output:
        JSON object with the latest available price, day OHLC, previous close,
        volume, currency and exchange, or an error message if the instrument
        was not found.
    """
    symbol = symbol.strip().upper()
    logging.info(f"Getting stock quote for {symbol}")

    quote_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {"User-Agent": "Jarvis (personal assistant)"}
    params = {"interval": "1d", "range": "1d"}

    def _fetch() -> dict:
        response = requests.get(quote_url, headers=headers, params=params, timeout=10)
        # Yahoo returns a JSON error body (with HTTP 404) for unknown symbols,
        # so we parse the body before deciding it is a hard failure.
        return response.json()

    try:
        data = await asyncio.to_thread(_fetch)
    except Exception as e:
        logging.error(f"Couldnt get quote for {symbol}")
        return {
            "message": f"Couldnt get a quote for {symbol}",
            "error": str(e),
        }

    chart = data.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        return {
            "error": f"Unknown or unsupported symbol '{symbol}'.",
            "tip": "For GPW stocks add the '.WA' suffix (e.g. 'PKO.WA'). For indices prefix with '^' (e.g. '^WIG20'). "
                   "Do NOT keep guessing ticker variants: Yahoo does not carry every instrument — many "
                   "ETFs listed on GPW have no '.WA' symbol at all. If the symbol isn't known for sure, "
                   "stop after one or two attempts and report that the quote is unavailable; if the user "
                   "or the request names a specific listing (e.g. a Euronext/Xetra cross-listing), use "
                   "that and convert the currency with get_exchange_rate.",
        }

    meta = chart["result"][0].get("meta", {})
    quote_time = meta.get("regularMarketTime")

    return {
        "symbol": meta.get("symbol", symbol),
        "price": meta.get("regularMarketPrice"),
        "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "volume": meta.get("regularMarketVolume"),
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "quote_time": datetime.fromtimestamp(quote_time).isoformat() if quote_time else None,
        "source": "Yahoo Finance",
    }


# Cap on how many daily points a history response returns verbatim; longer ranges are
# evenly downsampled so the model gets the shape without a huge token bill (the exact
# summary stats are always computed over the FULL range regardless).
_HISTORY_POINT_CAP = 160


@tool_ownership("finance_agent")
@function_tool
async def get_price_history(ctx: Context, instrument: str, period_days: int = 90) -> dict:
    """
    Description:
        Returns the DAILY HISTORY (a time-series over time, not just the current value) of a
        tracked financial instrument, read from Jarvis's own observations database. Use this
        whenever the user asks about a change/trend over time ("jak zmienił się kurs...",
        "ile urosło złoto w miesiąc", "S&P 500 przez ostatni rok") rather than the current
        level (for the live current value use get_exchange_rate / get_stock_quote).

        The database is populated daily at 23:00 and was backfilled one year back. The
        response includes both a code-computed summary (first/last/min/max/mean/% change over
        the requested window) and the underlying daily points (downsampled if very long).

    Parameters:
    ctx : Context
        Context in which the tool operates.

    instrument: str
        Which series to read. Accepts a canonical series id or common free text:
        - Currencies vs PLN: 'USD', 'EUR', 'GBP', 'CHF' (or 'USD/PLN', or 'fx:USD/PLN').
        - The owner's Amundi S&P 500 ETF in PLN: 'sp500' (canonical 'index:sp500_pln';
          the real fund's EUR price × EUR/PLN). Its raw EUR price: 'sp500_eur'
          (canonical 'etf:amundi_sp500_eur').
        - S&P 500 index in USD (price index): '^GSPC' (canonical 'index:^GSPC').
        - Metals in USD: 'gold'/'złoto' ('metal:GC=F'), 'silver'/'srebro' ('metal:SI=F').

    period_days: int = 90
        How far back to look, in calendar days (e.g. 30, 90, 365). Defaults to 90.

    Output:
        JSON with the resolved series id + label, the period, a `summary` (count, first, last,
        min, max, mean, change_pct) and `points` (list of {date, value}); or an error listing
        the available series if the instrument could not be resolved.
    """
    series = _history.resolve_series_alias(instrument)
    if series is None:
        return {
            "error": f"Nie rozpoznano instrumentu '{instrument}'.",
            "available": _history.series_catalog(),
        }

    try:
        period_days = max(1, int(period_days))
    except (TypeError, ValueError):
        period_days = 90
    start = date.today() - timedelta(days=period_days)

    def _read():
        return _observations.read_series(series, start=start)

    rows = await asyncio.to_thread(_read)
    if not rows:
        return {
            "series": series,
            "label": _history.series_catalog().get(series, series),
            "message": "Brak danych historycznych dla tego instrumentu w bazie (być może "
                       "kolektor jeszcze nie zebrał danych albo backfill nie został uruchomiony).",
        }

    summary = _history.summarize_series(rows)

    points = rows
    if len(rows) > _HISTORY_POINT_CAP:
        stride = len(rows) // _HISTORY_POINT_CAP + 1
        points = rows[::stride]
        if points[-1] is not rows[-1]:
            points.append(rows[-1])  # always keep the most recent point

    return {
        "series": series,
        "label": _history.series_catalog().get(series, series),
        "period_days": period_days,
        "summary": summary,
        "points": [{"date": r["date"], "value": r["value"]} for r in points],
        "note": "Wartości dzienne z bazy Jarvisa. Metale w USD; index:sp500_pln = realny "
                "Amundi S&P 500 UCITS ETF (klasa dystrybucyjna) w EUR × EUR/PLN; "
                "index:^GSPC to goły indeks cenowy w USD.",
    }
