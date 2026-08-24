"""finance_agent tools: FX rates (Frankfurter) and stock/index quotes (Yahoo Finance).
Registered into TOOLS_BY_AGENT["finance_agent"] via @tool_ownership."""

import logging
from datetime import datetime

import requests
from requests import HTTPError
from agents import RunContextWrapper, function_tool

from lib.core import Ctx, tool_ownership
from lib.agents.finance_agent.fx import validate_currency_code

logger = logging.getLogger(__name__)


@tool_ownership("finance_agent")
@function_tool
async def get_exchange_rate(ctx: RunContextWrapper[Ctx],
                            foreign_currency: str,
                            base_currency: str = "PLN") -> dict:
    f"""
    Description:
        This tool is used to obtain the current exchange rate between a given foreign and
        the base currency.

    Parameters:
    ctx : RunContextWrapper[Ctx]
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

    try:
        data = requests.get(f"https://api.frankfurter.dev/v1/latest?base={base_currency}&to={foreign_currency}")
        data_json = data.json()

        base = float(data_json["amount"])
        rate = float(data_json["rates"][foreign_currency])

        exchange_rate = base/rate

    except HTTPError as e:
        logging.error(f"Invalid request for Frankfurter API [base: {base_currency}, to: {foreign_currency}]")
        return {
            "message" : "Invalid request for Frankfurter API",
            "error" : e,
            "tip" : "Remember that the foreign_currency must be a correct three-letter currency code."
        }

    return {
        "message" : f"{base_currency}/{foreign_currency} exchange rate is {exchange_rate}"
    }

@tool_ownership("finance_agent")
@function_tool
async def get_stock_quote(ctx: RunContextWrapper[Ctx], symbol: str) -> dict:
    """
    Description:
        This tool returns the latest market quote (current price, day OHLC, previous
        close and volume) for a stock, ETF or index, using the free Yahoo Finance
        data source. It covers the Polish stock market (GPW) as well as global markets.

    Parameters:
    ctx : RunContextWrapper[Ctx]
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

    try:
        response = requests.get(quote_url, headers=headers, params=params, timeout=10)
        # Yahoo returns a JSON error body (with HTTP 404) for unknown symbols,
        # so we parse the body before deciding it is a hard failure.
        data = response.json()
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
            "tip": "For GPW stocks add the '.WA' suffix (e.g. 'PKO.WA'). For indices prefix with '^' (e.g. '^WIG20').",
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
