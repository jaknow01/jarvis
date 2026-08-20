from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.memory import memory
from lib.smart_device import SmartDevice, RGB, Mode
from lib.tools_utils import simplify_directions_response, get_forecast, validate_currency_code
from typing import List, Literal, Optional, Union
from lib.smart_device import SmartDevice, RGB, Mode
from lib.tools_utils import simplify_directions_response
from typing import List, Literal, Optional, Union, Annotated
import json
import asyncio
import googlemaps
import os
from datetime import datetime
import logging
from pyowm import OWM
import requests
from requests import HTTPError
from tavily import TavilyClient

TOOLS_BY_AGENT: dict[str: list[str]] = {}
DEVICES_PARAMS_PATH = "data/smart_device_data/smart_devices.json"
DEVICES_PREFERENCES_PATH = "data/smart_device_data/preferences.json"
MAPS_PARAMS_PATH = "data/maps_data/maps_memory.json"

logger = logging.getLogger(__name__)


def tool_ownership(agent_name: str):
    def wrapper(function_tool):
        if agent_name in TOOLS_BY_AGENT:
            TOOLS_BY_AGENT[agent_name].append(function_tool)
        else:
            TOOLS_BY_AGENT[agent_name] = [function_tool]
        return function_tool
    return wrapper

# ------- iot operator -------

# The model refers to devices only by their human-readable NAME. These helpers map a
# name back to the real SmartDevice (which holds ip/local_key) on the server side, so
# connection secrets are never exposed to or round-tripped through the LLM.

async def _load_devices(ctx: RunContextWrapper[Ctx]) -> dict:
    """Load the device registry (name -> SmartDevice) into the context."""
    with open(DEVICES_PARAMS_PATH, "r", encoding="utf-8") as f:
        configs = json.load(f)["list_of_elements"]
    devices: dict = {}
    for c in configs:
        try:
            dev = await SmartDevice.create_from_json(c)
            devices[dev.name] = dev
        except Exception as e:
            logging.error(f"Error creating device: {e}")
    ctx.context.devices = devices
    return devices


async def _ensure_devices(ctx: RunContextWrapper[Ctx]) -> dict:
    if not getattr(ctx.context, "devices", None):
        await _load_devices(ctx)
    return ctx.context.devices


def _resolve_devices(ctx: RunContextWrapper[Ctx], names: List[str]):
    """Map model-supplied device names to real devices. Returns (found, unknown_names)."""
    registry = getattr(ctx.context, "devices", None) or {}
    lower = {name.lower(): dev for name, dev in registry.items()}
    found, unknown = [], []
    for n in names:
        dev = registry.get(n) or lower.get((n or "").strip().lower())
        if dev is not None:
            found.append(dev)
        else:
            unknown.append(n)
    return found, unknown


def _unknown_device_error(ctx: RunContextWrapper[Ctx], name: str) -> dict:
    return {
        "Error": f"Unknown device '{name}'.",
        "available_devices": list((getattr(ctx.context, "devices", None) or {}).keys()),
    }


@tool_ownership("iot_operator")
@function_tool
async def get_devices_state(ctx: RunContextWrapper[Ctx]):
    """
    Description:
        This tool is used to download initial neccessary data about all smart devices from a database.
        It is then used to establish connection and check their current states.
    Note:
        This tool should only be run at the beginning of agent's tool calls. This provides an initial scan
        but due to accessing of the database it has a large overhead therefore it should only be run once.
        Devices are identified by their name; use those names with the other device tools.
    """
    logger.info("Checking all available devices")
    devices = await _load_devices(ctx)

    logger.info("Loading user preferences")
    with open(DEVICES_PREFERENCES_PATH, "r", encoding="utf-8") as f:
        preferences = json.load(f)
    ctx.context.devices_preferences = preferences

    states = await asyncio.gather(*(d.get_status() for d in devices.values()))
    ctx.context.devices_states = states

    return {"states" : states, "known_user_preferences": preferences}

@tool_ownership("iot_operator")
@function_tool
async def get_one_device_status(ctx: RunContextWrapper[Ctx], device_name: str) -> dict:
    """
    Description:
    This tool is used to check the status of a given device without the unnecessary overhead
    of checking all devices in the system. It should be used as an intermediate tool between tool calls
    instead of the tool get_devices_state.

    Note:
        When agents wants to interact with multiple devices this tool should be run in parallel.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device_name : str
        The name of the device whose status should be checked (as returned by get_devices_state).

    Output:
        State of the given device
    """
    logger.info(f"Checking status of {device_name}")
    await _ensure_devices(ctx)
    found, _ = _resolve_devices(ctx, [device_name])
    if not found:
        return _unknown_device_error(ctx, device_name)

    state = await found[0].get_status()
    ctx.context.devices_states[found[0].get_name()] = state
    return state

@tool_ownership("iot_operator")
@function_tool
async def turn_on_devices(ctx: RunContextWrapper[Ctx], device_names: List[str]) -> dict:
    """
    Description:
    This tool is used to turn on all mentioned devices.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device_names : List[str]
        Names of the devices that should be turned on (as returned by get_devices_state).

    Output:
    This tool returns the new states of the affected devices (and any names it did not recognize)
    """
    logger.info(f"Turning on devices: {device_names}")
    await _ensure_devices(ctx)
    found, unknown = _resolve_devices(ctx, device_names)

    await asyncio.gather(*(dev.turn_on() for dev in found))
    new_states = await asyncio.gather(*(dev.get_status() for dev in found))

    result: dict = {"states": new_states}
    if unknown:
        result["unknown_devices"] = unknown
    return result

@tool_ownership("iot_operator")
@function_tool
async def turn_off_devices(ctx: RunContextWrapper[Ctx], device_names: List[str]) -> dict:
    """
    Description:
    This tool is used to turn off all mentioned devices.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device_names : List[str]
        Names of the devices that should be turned off (as returned by get_devices_state).

    Output:
    This tool returns the new states of the affected devices (and any names it did not recognize)
    """
    logger.info(f"Turning off devices: {device_names}")
    await _ensure_devices(ctx)
    found, unknown = _resolve_devices(ctx, device_names)

    await asyncio.gather(*(dev.turn_off() for dev in found))
    new_states = await asyncio.gather(*(dev.get_status() for dev in found))

    result: dict = {"states": new_states}
    if unknown:
        result["unknown_devices"] = unknown
    return result

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def change_lighting_mode(ctx: RunContextWrapper[Ctx], device_name: str, new_mode: Mode) -> dict:
    """
    Description:
    This tool is used to change the lighting mode of a given smart device. Lighting mode can either
    be set to white or colour mode. When in colour mode various rgb settings can be applied to the
    device. When in white mode the lighting temperature can be adjusted.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device_name: str
        The name of the device that is to be affected by the mode change.

    new_mode: Mode
        The mode that will be applied to the chosen device
    """
    logger.info(f"Changing lighting mode of {device_name} to {new_mode.mode}")
    await _ensure_devices(ctx)
    found, _ = _resolve_devices(ctx, [device_name])
    if not found:
        return _unknown_device_error(ctx, device_name)
    return await found[0].change_mode(new_mode)

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def change_color(ctx: RunContextWrapper[Ctx], device_name: str, new_color: RGB) -> dict:
    """
    Description:
    This tool is used to change the colour of the given smart device.
    In order to set a new RGB value device must be in 'colour' lighting mode.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device_name: str
        The name of the device that is to be affected by the color change.
        Note: this device must be in 'colour' lighting mode in order for the change to be possible

    new_color: RGB
        The new color that the device will be set to as an RGB value.
        RGB values are integers from 0 to 255 where R = red, G = green, B = blue

    Output:
        This tool returns short information whether the attempt was successful
    """
    logger.info(f"Changing color of {device_name} to R={new_color.R} G={new_color.G} B={new_color.B}")
    await _ensure_devices(ctx)
    found, _ = _resolve_devices(ctx, [device_name])
    if not found:
        return _unknown_device_error(ctx, device_name)
    return await found[0].change_color(new_color)

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def change_light_temperature(ctx: RunContextWrapper, device_name: str, new_temp: Annotated[int, "range 0-1000"]) -> dict:
    """
    Description:
    This tool is used to change the colour temperature of the given device.
    In order to set a new color temperature the device must be in 'white' lighting mode.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device_name: str
        The name of the device that is to be affected by the lighting temperature change.
        Note: this device must be in 'white' lighting mode in order for the change to be possible

    new_temp: Annotated[int, "range 0-1000"]
        This parameter controls the temperature value where 0 is the brightest and 1000 the coldest

    Output:
        This tool returns short information whether the attempt was successful
    """
    logger.info(f"Changing lighting temperature of {device_name}")
    await _ensure_devices(ctx)
    found, _ = _resolve_devices(ctx, [device_name])
    if not found:
        return _unknown_device_error(ctx, device_name)
    return await found[0].change_temperature(new_temp)

# ------- maps agent -------

@tool_ownership("maps_agent")
@function_tool
async def get_maps_memory(ctx: RunContextWrapper[Ctx]) -> dict:
    """
    Description:
    This tool is used to download necessary maps data such as favourite places,
    known routes and other information which will facilitate understanding user's
    query in natural language.
    """
    logging.info("Checking known adresses")
    with open(MAPS_PARAMS_PATH, "r", encoding="utf-8") as f:
        list_of_jsons = json.load(f)
    
    ctx.context.known_adresses = list_of_jsons

    return list_of_jsons

@tool_ownership("maps_agent")
@function_tool
async def get_route_details(ctx: RunContextWrapper[Ctx],
                            origin: str,
                            destination: str,
                            transport_mode: Literal["driving", "walking", "bicycling", "transit"] = "transit",
                            transit_mode: Optional[Literal["bus", "subway", "tram", None]] = None,
                            departure_time: Optional[Union[str, datetime]] = "now",
                            #arrival_time: Optional[str] = None,
                            show_alternatives: Optional[bool] = True
                            ) -> dict:
    """
    Description:
    This tool is used to calculate the route between origin and destination based on the user's preferred
    mode of transport (such as car, transit, etc.) and return the most optimal route to the user.
    Unless specified otherwise one should always assume that both origin and destination are in Warsaw, Poland.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates
    
    origin: str
        The starting point of the journey. This can be a specific adress, specific bus/metro/train stop
        ,a known landmark or a point from navigation memory.

    destination: str
        The end of the journey. This can be a specific adress, specific bus/metro/train stop
        ,a known landmark or a point from navigation memory.

    transport_mode: Literal["driving", "walking", "bicycling", "transit"] = 'transit'
        User's preffered mode of communication such as 'car', 'transit' etc.
        Note: should remain with defalut value 'transit' unless user specifies otherwise

    transit_mode: Optional[Literal["bus", "subway", "tram", None]] = None
        Limits the public transit options to only one specified mode. When left with default value of None
        the route may consist of any combination of public transport modes such as buses, trams, subways etc.
        If a given mode is specified the route will be limited to only one mode of public transport.
        Note: this parameter can only be provided if transport_mode = 'transit'. Otherwise it should remain None

    departure_time: Optional[Union[str, datetime]] = "now"
        Time at which user wishes to leave. By default is set to 'now'.

    show_alternatives: Optional[bool] = True
        This parameter controls whether the navigation API returns only one most optimal route
        or multiple options.
        When True only one route is returned, otherwise multiple options
        Note: it sholud remain True unless user specifies otherwise

    Output:
        This function returns a json file with all of the steps of the most optimal route from origin to destination
        along with all transfers if necessary. Should an error occurr this function will return a json with the
        proper error message.
    
    Note:
        The user is a fast-walker therefore you should assume that all distances that require traveling on foot will
        be covered in 1.25x faster than the navigation data suggests.
    """

    gmaps_client = googlemaps.Client(os.getenv("GOOGLE_MAPS_API_KEY"))

    if transport_mode != "transit" and transit_mode is not None:
        transit_mode = None

    logging.info("Starting route planning")

    try:
        directions_result = gmaps_client.directions(
            origin=origin,
            destination=destination,
            mode=transport_mode,
            transit_mode=transit_mode,
            departure_time=departure_time,
            alternatives=show_alternatives
        )

        result = await simplify_directions_response(directions_result)
    except Exception as e:
        logging.error("Error while getting routes from Google")
        return {
            "Message" : "Error while getting routes from Google",
            "Error": e
        }

    return result
                     
# ------- finance agent -------

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

# ------- weather agent -------

@tool_ownership("weather_agent")
@function_tool
async def current_weather(ctx: RunContextWrapper[Ctx], city: str = "Warsaw") -> dict:
    """
    Description:
        This tool is used to get the current weather conditions in a specified city.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    city: str = "Warsaw"
        Name of the city where the weather conditions are to be checked.
        Unless specified otherwise by the user the default city is Warsaw.
        Return the city name in nominative form (base form) — do not inflect or decline it.

    Output:
        JSON object with current weather conditions in a specified place.
    """

    owm_client = OWM(os.getenv("OPENWEATHER_API_KEY"))
    owm_manager = owm_client.weather_manager()

    logging.info(f"Getting weather at {city}")

    try:
        current_weather = owm_manager.weather_at_place(city)
    except Exception as e:
        logging.error(f"Couldnt get weather at {city}")
        return {"message": f"Couldnt get weather at {city}",
                "exception": e}

    return current_weather

@tool_ownership("weather_agent")
@function_tool
async def get_current_date_and_time(ctx: RunContextWrapper[Ctx]) -> dict:
    """
    Description:
        This tool is used to obtain today's date and current time. It is neccessary
        to use it before getting the weather forecasts otherwise agent will not be
        able to process user's request properly when it comes to dates and time.

    Output:
        JSON object with the current local date, time, weekday and ISO 8601 timestamp.
    """
    now = datetime.now()
    logger.info("Providing current date and time")
    now_params = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "iso": now.isoformat(),
    }
    # Stored on the context so weather_forecast can enforce that this ran first.
    ctx.context.time_date_now = now_params
    return now_params

@tool_ownership("weather_agent")
@function_tool
async def weather_forecast(ctx: RunContextWrapper[Ctx],
                           forecast_days: Literal["1", "3", "7"],
                           forecast_type: Literal["hourly", "daily"],
                           city: str = "Warsaw"
                           ) -> dict:
    """
    Important: 

    Description:
        This tool is used to check a current weather forecast in a given location.
        It can be either a short-term (min 3 hours) or a long-term (max 5 days) forecast
        with different granularity (3h or daily intervals).

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates
 
    forecast_days: Literal["1", "3", "7"]
        How long into the future should the forecast reach measured in days.

    forecast_type: Literal["hourly", "daily"]
        Time intervals in which the forecast will be divided. When asking for a short-term forecast
        more granular data obtained with 'hourly' may be more optimal wheras for long-term forecast
        it usually is better to provide 'daily' intervals.

    city: str = "Warsaw"
        Name of the city where the weather conditions are to be checked.
        Unless specified otherwise by the user the default city is Warsaw. The city name should be in polish.
        Return the city name in nominative form (base form) — do not inflect or decline it.

    Output:
        JSON object with the weather forecast made according to specifications
    """
    if not ctx.context.time_date_now:
        return {
            "Message": "You don't know the current date, weekday and time - the forecast could be inaccurate.",
            "Tip": "Run the get_current_date_and_time tool first, then call this tool again.",
        }

    multiple_results = False
    geolocation_url = f"https://nominatim.openstreetmap.org/search?q={city}&format=json"

    headers = {
        "User-Agent":"Jarvis (lkc86484@laoia.com)",
        "Accept":"application/json"
    }

    logging.info(f"Starting geolocation for {city}")
    try:
        geolocation = requests.get(geolocation_url, headers=headers)
        geolocation = geolocation.json()
        logging.info("Geolocation obtained")
    except HTTPError as e:
        logging.error(f"Couldnt geolocate {city} - issue with API")
        return {"message" : f"Couldnt geolocate this location {city}",
                "status_code" : e}

    output = [
        {"name" : result["display_name"], "long" : result["lon"], "lat" : result["lat"]}
        for result in geolocation
    ]

    if len(output) > 1:
        logging.info("Found more than one geolocation")
        multiple_results = True
        if len(output) > 3:
            logging.info("Found more than three geolocations")
            output = output[:3]

    logging.info(f"Getting {forecast_type} in {forecast_days} intervals")
    tasks = [
        get_forecast(p, forecast_days, forecast_type)
        for p in output
    ]

    forecasts = await asyncio.gather(*tasks)

    result = {
        "Message" : f"Successfully obtained weather forecasts for {city}",
        "Forecast" : forecasts
    }

    if multiple_results:
        location_names = ",".join([l["name"] for l in output])
        result["Note"] = f"Multiple geolocations have been found for {city}.\
            If they are not actually the same city listed out multiple times inform the user about this.\
                Location names: {location_names}"

    return result

# ------- news agent -------
 
@tool_ownership("news_agent")
@function_tool
async def search_news(ctx: RunContextWrapper[Ctx],
                               query: str,
                               topic: Literal["news", "finance"],
                               search_depth: Literal["basic", "advanced"] = "advanced"
                               ) -> dict:
    """
    Description:
        This tool is used to crawl through varius reputable news sources in search for current and historical events to
        find an answer to the user's query.
    
    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    query: str
        Defines the matter or event that interests the user. Should be structured in natural language
        and reflect exactly what the user wants to know.
        For better search results this parameter
        **must** be written entirely in english as it's the most optimal language for the search engine

    topic: Literal["news", "finance"]
        Controls what is the general topic of the user's query. All political, sports, history-related etc. queries should be designated as 'news'
        wheras all stock market, commodities, currencies, cryptocurrencies related queries etc. should be treated as 'finance'

    search_depth: Literal["basic", "advanced"] = "advanced"
        The level of detail that should be extracted by the search engine.
        Value 'basic' should be used when user requests a quick and short summary - should only
        be used when user clearly asks for a short anwser.
        Value 'advanced' is the default value which provides more details.
    """
    logging.info(f"Starting news search with query {query} at {search_depth} depth")

    client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    # general search
    response = client.search(
        query=query,
        include_answer="advanced",
        search_depth=search_depth,
        topic=topic
    )

    # reputable source
    response_reputable = client.search(
        query=query,
        include_answer="advanced",
        search_depth=search_depth,
        topic="news",
        include_domains=["reuters.com"] if topic == "news" else ["bloomberg.com"]
    )

    result = {
        "general_search": response,
        "reputable source": response_reputable
    }

    return result

# ------- memory operator -------

@tool_ownership("memory_operator")
@function_tool
async def get_memory(ctx: RunContextWrapper[Ctx], category: Optional[str] = None) -> dict:
    """
    Description:
        Retrieve the user's stored long-term memory (durable preferences, facts,
        habits, interests, routines). Use this to personalize answers and to avoid
        re-asking things the user already told you.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    category: Optional[str]
        If given, return only entries in this category (e.g. 'preferences',
        'facts', 'habits', 'interests', 'routines'). If omitted, return everything.

    Output:
        JSON object with the matching memory entries and their count.
    """
    logging.info(f"Reading long-term memory (category={category or 'all'})")
    entries = memory.by_category(category) if category else memory.all()
    return {"entries": entries, "count": len(entries)}

@tool_ownership("memory_operator")
@function_tool
async def save_memory(ctx: RunContextWrapper[Ctx],
                      text: str,
                      category: str = "preferences",
                      source: Literal["user", "inferred"] = "user",
                      confidence: Literal["high", "medium", "low"] = "high") -> dict:
    """
    Description:
        Store a new durable memory about the user. Use it when the user states a
        lasting preference or fact, or when you reliably infer one. Write a concise,
        self-contained statement in natural language. Do NOT store transient or
        one-off details.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    text: str
        The memory to store, as a single self-contained natural-language sentence.

    category: str = "preferences"
        Grouping label. Suggested: 'preferences', 'facts', 'habits', 'interests',
        'routines'.

    source: Literal["user", "inferred"] = "user"
        'user' when the user stated it explicitly; 'inferred' when you concluded it.

    confidence: Literal["high", "medium", "low"] = "high"
        How sure you are of this memory.

    Output:
        JSON object with the saved entry (including its generated id).
    """
    logging.info(f"Saving long-term memory (category={category}, source={source})")
    try:
        entry = memory.add(text, category=category, source=source, confidence=confidence)
    except ValueError as e:
        return {"Error": str(e)}
    return {"saved": entry}

@tool_ownership("memory_operator")
@function_tool
async def update_memory(ctx: RunContextWrapper[Ctx],
                        entry_id: str,
                        text: Optional[str] = None,
                        category: Optional[str] = None,
                        confidence: Optional[Literal["high", "medium", "low"]] = None) -> dict:
    """
    Description:
        Correct or refine an existing memory entry, identified by its id (from
        get_memory). Only the provided fields are changed.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    entry_id: str
        The id of the entry to update (e.g. 'mem_1a2b3c4d').

    text: Optional[str]
        New text, if changing it.

    category: Optional[str]
        New category, if changing it.

    confidence: Optional[Literal["high", "medium", "low"]]
        New confidence, if changing it.

    Output:
        JSON object with the updated entry, or an error if the id was not found.
    """
    logging.info(f"Updating long-term memory {entry_id}")
    entry = memory.update(entry_id, text=text, category=category, confidence=confidence)
    return {"updated": entry} if entry else {"Error": f"No memory entry with id '{entry_id}'"}

@tool_ownership("memory_operator")
@function_tool
async def delete_memory(ctx: RunContextWrapper[Ctx], entry_id: str) -> dict:
    """
    Description:
        Permanently remove a memory entry by its id (from get_memory). Use when a
        memory is wrong or no longer relevant.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    entry_id: str
        The id of the entry to delete (e.g. 'mem_1a2b3c4d').

    Output:
        JSON object confirming deletion, or an error if the id was not found.
    """
    logging.info(f"Deleting long-term memory {entry_id}")
    return {"deleted": entry_id} if memory.delete(entry_id) else {"Error": f"No memory entry with id '{entry_id}'"}













