from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.memory import memory
from lib.smart_device import SmartDevice, RGB, Mode
from lib.tuya_link import manager, SCAN_TIMEOUT
from lib.tools_utils import (
    simplify_directions_response, get_forecast, validate_currency_code, normalize_departure_time,
    fetch_fpl, fetch_fpl_bootstrap, index_bootstrap, resolve_gameweek, relevant_gameweek,
    describe_player, summarize_fixture_events)
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


async def _wake_and_heal_ips(devices: dict) -> None:
    """Run one broadcast scan up front to wake the fleet before we probe it, and
    correct any drifted IPs from the scan result.

    Cold Tuya bulbs often refuse direct TCP until a broadcast scan has seen them
    (docs/TUYA_LOCAL.md, finding #1). Probing all devices concurrently without this
    wake makes the marginal ones fail EHOSTUNREACH on first contact even though they
    are perfectly healthy once woken (confirmed by smoke-tests/probe.py, which scans
    first and then sees 100% health). The scan is serialized+coalesced in tuya_link,
    so this is one cheap scan shared across the burst."""
    id_to_ip = await asyncio.to_thread(manager.scan, SCAN_TIMEOUT, "wake before status sweep")
    for dev in devices.values():
        ip = id_to_ip.get(dev.dev_id)
        if ip and ip != dev.ip:
            logger.info(f"{dev.name}: IP drift {dev.ip} -> {ip} (from wake scan)")
            dev.ip = ip


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

    # Wake the fleet with one broadcast scan before probing all devices concurrently;
    # without it, cold/marginal bulbs refuse first-contact TCP and fail spuriously
    # even though they are healthy (see _wake_and_heal_ips / docs/TUYA_LOCAL.md).
    await _wake_and_heal_ips(devices)

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

# The model works with place ALIASES only (Home, work, University, ...). The mapping
# of an alias to its real street address is resolved on the server side inside
# get_route_details, so the user's actual home/work addresses are never handed to the
# LLM as a browsable address book.

def _load_maps_memory(ctx: RunContextWrapper[Ctx]) -> dict:
    with open(MAPS_PARAMS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    ctx.context.known_adresses = data
    return data


def _known_entries(ctx: RunContextWrapper[Ctx]) -> list:
    data = getattr(ctx.context, "known_adresses", None) or {}
    if not data:
        try:
            data = _load_maps_memory(ctx)
        except Exception as e:
            logging.error(f"Could not load maps memory: {e}")
            return []
    # tolerate the historical key spelling in the stored file
    for key in ("known_adressess", "known_addresses", "known_adresses"):
        if isinstance(data.get(key), list):
            return data[key]
    for value in data.values():
        if isinstance(value, list):
            return value
    return []


def _resolve_place(ctx: RunContextWrapper[Ctx], name: str):
    """Map a place name to (real_address, matched_alias). A known alias resolves to
    its stored address (server-side); an unknown name passes through unchanged."""
    if not name:
        return name, None
    target = name.strip().lower()
    for entry in _known_entries(ctx):
        if target in [a.lower() for a in entry.get("aliases", [])]:
            return entry.get("address", name), name
    return name, None


@tool_ownership("maps_agent")
@function_tool
async def get_maps_memory(ctx: RunContextWrapper[Ctx]) -> dict:
    """
    Description:
    This tool lists the user's known/favourite places by their ALIAS (e.g. 'Home',
    'work', 'University'). Use these aliases as origin/destination in get_route_details
    — the real street address behind an alias is resolved automatically and is not
    needed (and not shown) here.
    """
    logging.info("Listing known place aliases")
    aliases = []
    for entry in _known_entries(ctx):
        aliases.extend(entry.get("aliases", []))
    return {
        "known_place_aliases": aliases,
        "note": "Refer to these places by alias; their actual addresses are resolved automatically.",
    }

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
        The starting point of the journey. This can be an alias of a known place
        (from get_maps_memory, e.g. 'Home'), a specific bus/metro/train stop, or a
        landmark. Known aliases are resolved to their real address automatically.

    destination: str
        The end of the journey. This can be an alias of a known place (from
        get_maps_memory, e.g. 'work'), a specific bus/metro/train stop, or a landmark.
        Known aliases are resolved to their real address automatically.

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

    # Resolve known-place aliases to real addresses server-side; keep the alias so we
    # can relabel the endpoints in the response and avoid returning the address.
    origin_address, origin_alias = _resolve_place(ctx, origin)
    destination_address, destination_alias = _resolve_place(ctx, destination)

    # Google accepts only "now" or an int Unix timestamp here; a raw model-supplied
    # time string (ISO, "8:00", …) otherwise 400s. Normalize before the call.
    departure_time = normalize_departure_time(departure_time)

    logging.info("Starting route planning")

    try:
        directions_result = gmaps_client.directions(
            origin=origin_address,
            destination=destination_address,
            mode=transport_mode,
            transit_mode=transit_mode,
            departure_time=departure_time,
            alternatives=show_alternatives
        )

        result = await simplify_directions_response(directions_result)
    except Exception as e:
        logging.error("Error while getting routes from Google", exc_info=True)
        return {
            "Message" : "Error while getting routes from Google",
            "Error": str(e)
        }

    # Relabel endpoints back to the alias so the user's real addresses are not exposed.
    for leg in result:
        if origin_alias:
            leg["start_address"] = origin_alias
        if destination_alias:
            leg["end_address"] = destination_alias

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

# ------- fpl agent (Fantasy Premier League) -------
#
# All data comes from the keyless public FPL API. Numeric team/player/gameweek ids are
# resolved to human-readable names server-side (via the bootstrap-static reference
# data), so the model — and the user — only ever deal with names, not raw ids.
#
# The owner's own manager ("entry") id and default league id are read from the env
# (FPL_ENTRY_ID / FPL_LEAGUE_ID), like the other API config. Each tool also accepts an
# explicit id override for ad-hoc lookups.

def _fpl_entry_id(override: Optional[int]) -> Optional[int]:
    if override is not None:
        return int(override)
    raw = os.getenv("FPL_ENTRY_ID")
    return int(raw) if raw and raw.strip().isdigit() else None


def _fpl_league_id(override: Optional[int]) -> Optional[int]:
    if override is not None:
        return int(override)
    raw = os.getenv("FPL_LEAGUE_ID")
    return int(raw) if raw and raw.strip().isdigit() else None


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_fixtures(ctx: RunContextWrapper[Ctx], gameweek: Optional[int] = None) -> dict:
    """
    Description:
        Lists the Premier League fixtures (matches) for a Fantasy Premier League
        gameweek, with each team's official FDR difficulty rating (1 = easiest,
        5 = hardest) and kickoff time. Use this to answer "what are today's / the
        upcoming matches / this round" questions.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    gameweek: Optional[int] = None
        The gameweek number to list fixtures for. If omitted, the round currently in
        play is used: the current gameweek while it is still running (which is the one
        that contains "today's" matches), or the next gameweek once the current one has
        finished. To pinpoint a specific day, read each fixture's kickoff_time and
        compare it to today's date from the environment context.

    Output:
        JSON object with the resolved gameweek and a list of fixtures. Each fixture
        gives the home/away team names, their difficulty ratings, kickoff time, and —
        if the match has been played — the final score.
    """
    logger.info(f"Getting FPL fixtures for gameweek={gameweek or 'in-play'}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = gameweek if gameweek is not None else relevant_gameweek(bootstrap)
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, _, _ = index_bootstrap(bootstrap)
        fixtures = await fetch_fpl(f"fixtures/?event={gw}")
    except Exception as e:
        logger.error("Error while getting FPL fixtures", exc_info=True)
        return {"Message": "Error while getting FPL fixtures", "Error": str(e)}

    matches = []
    for f in fixtures:
        home = teams_by_id.get(f.get("team_h"), {})
        away = teams_by_id.get(f.get("team_a"), {})
        match = {
            "home": home.get("name"),
            "away": away.get("name"),
            "home_difficulty": f.get("team_h_difficulty"),
            "away_difficulty": f.get("team_a_difficulty"),
            "kickoff_time": f.get("kickoff_time"),
            "finished": f.get("finished"),
        }
        if f.get("finished") or f.get("started"):
            match["score"] = f"{f.get('team_h_score')}-{f.get('team_a_score')}"
        matches.append(match)

    return {"gameweek": gw, "fixtures_count": len(matches), "fixtures": matches}


@tool_ownership("fpl_agent")
@function_tool
async def get_pl_teams(ctx: RunContextWrapper[Ctx]) -> dict:
    """
    Description:
        Lists all Premier League teams in the current Fantasy Premier League season,
        with their short codes and FPL strength ratings (overall/attack/defence,
        split home/away, on a 1-5 scale). Use this for questions about the teams
        themselves (who is in the league, relative strength).

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    Output:
        JSON object with a list of teams (name, short_name, and strength ratings).
    """
    logger.info("Getting Premier League teams")
    try:
        bootstrap = await fetch_fpl_bootstrap()
    except Exception as e:
        logger.error("Error while getting PL teams", exc_info=True)
        return {"Message": "Error while getting Premier League teams", "Error": str(e)}

    teams = [
        {
            "name": t.get("name"),
            "short_name": t.get("short_name"),
            "strength_overall_home": t.get("strength_overall_home"),
            "strength_overall_away": t.get("strength_overall_away"),
            "strength_attack_home": t.get("strength_attack_home"),
            "strength_attack_away": t.get("strength_attack_away"),
            "strength_defence_home": t.get("strength_defence_home"),
            "strength_defence_away": t.get("strength_defence_away"),
        }
        for t in bootstrap.get("teams", [])
    ]
    return {"teams_count": len(teams), "teams": teams}


@tool_ownership("fpl_agent")
@function_tool
async def get_my_fpl_squad(ctx: RunContextWrapper[Ctx],
                           gameweek: Optional[int] = None,
                           entry_id: Optional[int] = None) -> dict:
    """
    Description:
        Returns the owner's Fantasy Premier League squad (the 15 players picked) for a
        gameweek, with each player resolved to name/team/position, the captain and
        vice-captain marked, and the starting XI vs. bench split. Also reports that
        gameweek's points, squad value and money in the bank.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    gameweek: Optional[int] = None
        The gameweek to fetch the squad for. If omitted, the current gameweek is used.
        Note: a squad only becomes public after that gameweek's deadline has passed.

    entry_id: Optional[int] = None
        The FPL manager ("entry") id to look up. If omitted, the owner's own id from
        the FPL_ENTRY_ID environment variable is used.

    Output:
        JSON object with the resolved gameweek, a per-gameweek summary (points, bank,
        squad value) and the list of picked players (starting XI first, then bench),
        each flagged with captaincy and whether they are on the bench.
    """
    eid = _fpl_entry_id(entry_id)
    if eid is None:
        return {
            "Error": "No FPL manager id is configured.",
            "Tip": "Set FPL_ENTRY_ID in the environment (your manager id, e.g. from the "
                   "URL fantasy.premierleague.com/entry/<ID>/event/1) or pass entry_id explicitly.",
        }
    logger.info(f"Getting FPL squad for entry={eid}, gameweek={gameweek or 'current'}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, gameweek, prefer="current")
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, positions_by_id = index_bootstrap(bootstrap)
        picks_data = await fetch_fpl(f"entry/{eid}/event/{gw}/picks/")
    except Exception as e:
        logger.error("Error while getting FPL squad", exc_info=True)
        return {
            "Message": "Error while getting the FPL squad",
            "Error": str(e),
            "Tip": "The squad is only public after that gameweek's deadline. Check the "
                   "manager id and that the gameweek has started.",
        }

    if isinstance(picks_data, dict) and picks_data.get("detail"):
        return {"Error": f"FPL API: {picks_data['detail']}", "entry_id": eid, "gameweek": gw}

    history = picks_data.get("entry_history", {}) or {}
    players = []
    for p in picks_data.get("picks", []):
        element = elements_by_id.get(p.get("element"), {})
        info = describe_player(element, teams_by_id, positions_by_id)
        # positions 1-11 are the starting XI, 12-15 the bench (in bench order)
        info["on_bench"] = p.get("position", 0) > 11
        info["is_captain"] = p.get("is_captain", False)
        info["is_vice_captain"] = p.get("is_vice_captain", False)
        info["multiplier"] = p.get("multiplier")
        players.append(info)

    return {
        "entry_id": eid,
        "gameweek": gw,
        "summary": {
            "gameweek_points": history.get("points"),
            "total_points": history.get("total_points"),
            "overall_rank": history.get("overall_rank"),
            "bank": round(history.get("bank", 0) / 10, 1),          # £m
            "squad_value": round(history.get("value", 0) / 10, 1),  # £m
            "transfers_made": history.get("event_transfers"),
        },
        "squad": players,
    }


@tool_ownership("fpl_agent")
@function_tool
async def get_my_fpl_leagues(ctx: RunContextWrapper[Ctx], entry_id: Optional[int] = None) -> dict:
    """
    Description:
        Lists the classic (points-based) mini-leagues the owner's Fantasy Premier League
        manager is a member of, with the manager's current rank in each. Use this to
        discover league ids (e.g. to then fetch a specific league's standings) or to
        answer "which leagues am I in / what's my rank".

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    entry_id: Optional[int] = None
        The FPL manager ("entry") id. If omitted, the owner's own id from the
        FPL_ENTRY_ID environment variable is used.

    Output:
        JSON object with the manager's name and a list of their classic leagues
        (league id, name, and the manager's rank in it).
    """
    eid = _fpl_entry_id(entry_id)
    if eid is None:
        return {
            "Error": "No FPL manager id is configured.",
            "Tip": "Set FPL_ENTRY_ID in the environment or pass entry_id explicitly.",
        }
    logger.info(f"Getting FPL leagues for entry={eid}")
    try:
        entry = await fetch_fpl(f"entry/{eid}/")
    except Exception as e:
        logger.error("Error while getting FPL leagues", exc_info=True)
        return {"Message": "Error while getting the manager's leagues", "Error": str(e)}

    leagues = [
        {"id": l.get("id"), "name": l.get("name"), "my_rank": l.get("entry_rank")}
        for l in (entry.get("leagues", {}) or {}).get("classic", [])
    ]
    return {
        "manager_name": f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        "team_name": entry.get("name"),
        "leagues_count": len(leagues),
        "leagues": leagues,
    }


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_league_standings(ctx: RunContextWrapper[Ctx],
                                   league_id: Optional[int] = None,
                                   limit: int = 25) -> dict:
    """
    Description:
        Returns the standings (table) of a Fantasy Premier League classic mini-league:
        the ranked managers with their team name, total points and last-gameweek
        points. The owner's own row is flagged. Use this for "how's my league doing /
        what's the table in my league".

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    league_id: Optional[int] = None
        The classic league id to fetch. If omitted, the owner's default league from the
        FPL_LEAGUE_ID environment variable is used. Use get_my_fpl_leagues to discover
        league ids.

    limit: int = 25
        Maximum number of ranked entries to return (from the top of the table).

    Output:
        JSON object with the league name and the ranked standings (rank, team name,
        manager name, total points, last-gameweek points), with the owner's row marked.
    """
    lid = _fpl_league_id(league_id)
    if lid is None:
        return {
            "Error": "No FPL league id is configured.",
            "Tip": "Set FPL_LEAGUE_ID in the environment, pass league_id explicitly, or call "
                   "get_my_fpl_leagues to find your league ids.",
        }
    logger.info(f"Getting FPL standings for league={lid}")
    try:
        data = await fetch_fpl(f"leagues-classic/{lid}/standings/")
    except Exception as e:
        logger.error("Error while getting FPL league standings", exc_info=True)
        return {"Message": "Error while getting the league standings", "Error": str(e)}

    my_entry = _fpl_entry_id(None)
    results = (data.get("standings", {}) or {}).get("results", [])
    table = [
        {
            "rank": r.get("rank"),
            "team_name": r.get("entry_name"),
            "manager": r.get("player_name"),
            "total_points": r.get("total"),
            "gameweek_points": r.get("event_total"),
            "is_me": my_entry is not None and r.get("entry") == my_entry,
        }
        for r in results[: max(1, limit)]
    ]
    return {
        "league_id": lid,
        "league_name": (data.get("league", {}) or {}).get("name"),
        "entries_shown": len(table),
        "standings": table,
    }


def _player_match_status(team_state: dict, team_id) -> str:
    """Human-readable state of a player's match this gameweek."""
    st = team_state.get(team_id)
    if not st:
        return "not started"
    if st.get("live"):
        minutes = st.get("minutes")
        return f"live {minutes}'" if minutes is not None else "live"
    if st.get("finished"):
        return "finished"
    return "not started"


def _player_playing_status(match_state: dict, stats: dict) -> str:
    """Whether the player is actually on the pitch, from the live feed's `starts` /
    `minutes` (which only populate once the real lineup is confirmed at kickoff).

    A player can be in the owner's FPL XI yet start the real match on the bench: that
    shows here as 'benched (not on)' while the match is live, or 'did not play (unused
    sub)' once it has finished with zero minutes."""
    starts = stats.get("starts") or 0
    minutes = stats.get("minutes") or 0
    live = bool(match_state and match_state.get("live"))
    finished = bool(match_state and match_state.get("finished"))
    if not live and not finished:
        return "match not started"
    if finished:
        if minutes and starts:
            return "played (started)"
        if minutes:
            return "played (subbed on)"
        return "did not play (unused sub)"
    # match is live
    if starts:
        return "playing (started)"
    if minutes:
        return "playing (subbed on)"
    return "benched (not on)"


@tool_ownership("fpl_agent")
@function_tool
async def get_fpl_live(ctx: RunContextWrapper[Ctx],
                       gameweek: Optional[int] = None,
                       include_my_players: bool = True,
                       entry_id: Optional[int] = None) -> dict:
    """
    Description:
        Real-time state of the gameweek: which Premier League matches are being played
        right now, what is happening in them (live score, minute, goals, assists, cards)
        and — for the owner — how each of their FPL players is doing live (minutes,
        goals, bonus, live points, and whether that player's match is in play). Use this
        for "are there matches on now / what's the score / how are my players doing".

        Each of the owner's players also carries `started` and `playing_status`, which
        reflect the REAL match — 'playing (started)', 'playing (subbed on)', 'benched
        (not on)', 'played/did not play' — so you can tell a player who is actually on
        the pitch from one the owner has in their FPL XI but who is sitting on the real
        bench. `my_players.starters_not_playing` lists exactly those benched/unused XI
        players. This becomes known only once lineups are confirmed at kickoff.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    gameweek: Optional[int] = None
        The gameweek to inspect. If omitted, the round currently in play is used
        (the current gameweek while it is unfinished, else the next one).

    include_my_players: bool = True
        When true, also return the owner's squad with live per-player stats. Set false
        to only get the match scores/events (e.g. when no manager id is configured).

    entry_id: Optional[int] = None
        The FPL manager ("entry") id for the squad section. If omitted, the owner's own
        id from FPL_ENTRY_ID is used.

    Output:
        JSON object with `any_live` (is any match in play), `live_matches` (in-progress
        matches with live score, minute and events), `finished_matches` (final scores
        for matches already played this gameweek) and, when requested, `my_players`
        (each squad player's live stats, match status and provisional points).
    """
    logger.info(f"Getting FPL live state for gameweek={gameweek or 'in-play'}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = gameweek if gameweek is not None else relevant_gameweek(bootstrap)
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, positions_by_id = index_bootstrap(bootstrap)
        fixtures = await fetch_fpl(f"fixtures/?event={gw}")
    except Exception as e:
        logger.error("Error while getting FPL live state", exc_info=True)
        return {"Message": "Error while getting the live FPL state", "Error": str(e)}

    # A fixture is "live" once it has started but before it is provisionally finished
    # (finished_provisional flips to true at full time, before bonus is finalized).
    live_matches, finished_matches = [], []
    for f in fixtures:
        if not f.get("started"):
            continue
        home = teams_by_id.get(f.get("team_h"), {})
        away = teams_by_id.get(f.get("team_a"), {})
        base = {
            "home": home.get("name"),
            "away": away.get("name"),
            "score": f"{f.get('team_h_score')}-{f.get('team_a_score')}",
        }
        if f.get("finished_provisional"):
            finished_matches.append(base)
        else:
            live = dict(base)
            live["minutes"] = f.get("minutes")
            live["events"] = summarize_fixture_events(f, elements_by_id, teams_by_id)
            live_matches.append(live)

    result: dict = {
        "gameweek": gw,
        "any_live": bool(live_matches),
        "live_matches": live_matches,
        "finished_matches": finished_matches,
    }
    if not live_matches and not finished_matches:
        result["note"] = "No matches in this gameweek have kicked off yet."

    if include_my_players:
        eid = _fpl_entry_id(entry_id)
        if eid is None:
            result["my_players"] = {
                "Error": "No FPL manager id is configured.",
                "Tip": "Set FPL_ENTRY_ID in the environment or pass entry_id explicitly.",
            }
            return result
        try:
            live_data = await fetch_fpl(f"event/{gw}/live/")
            picks_data = await fetch_fpl(f"entry/{eid}/event/{gw}/picks/")
        except Exception as e:
            logger.error("Error while getting live squad performance", exc_info=True)
            result["my_players"] = {"Error": str(e)}
            return result

        live_by_element = {e.get("id"): e.get("stats", {}) for e in live_data.get("elements", [])}
        # Map each team to the state of its match this gameweek (prefer a live fixture
        # if a team somehow has more than one, e.g. a double gameweek).
        team_state: dict = {}
        for f in fixtures:
            info = {
                "live": bool(f.get("started") and not f.get("finished_provisional")),
                "finished": bool(f.get("finished_provisional")),
                "minutes": f.get("minutes"),
            }
            for team_id in (f.get("team_h"), f.get("team_a")):
                prev = team_state.get(team_id)
                if prev is None or (info["live"] and not prev["live"]):
                    team_state[team_id] = info

        players = []
        provisional_points = 0
        for p in picks_data.get("picks", []):
            element = elements_by_id.get(p.get("element"), {})
            stats = live_by_element.get(p.get("element"), {})
            team_id = element.get("team")
            multiplier = p.get("multiplier", 0)
            points = stats.get("total_points", 0) or 0
            counted = points * multiplier
            provisional_points += counted
            players.append({
                "name": element.get("web_name"),
                "team": teams_by_id.get(team_id, {}).get("short_name"),
                "position": positions_by_id.get(element.get("element_type"), {}).get("singular_name_short"),
                "on_bench": p.get("position", 0) > 11,  # on the owner's FPL bench
                "is_captain": p.get("is_captain", False),
                "is_vice_captain": p.get("is_vice_captain", False),
                "multiplier": multiplier,
                "match_status": _player_match_status(team_state, team_id),
                # whether the player actually featured in the real match (started / came
                # off the bench / benched) — distinct from the owner's FPL bench above.
                "started": bool(stats.get("starts")),
                "playing_status": _player_playing_status(team_state.get(team_id), stats),
                "minutes": stats.get("minutes"),
                "goals": stats.get("goals_scored"),
                "assists": stats.get("assists"),
                "yellow_cards": stats.get("yellow_cards"),
                "red_cards": stats.get("red_cards"),
                "bonus": stats.get("bonus"),
                "points": points,               # raw FPL points for the player
                "points_counted": counted,      # after captain/bench multiplier
            })

        # Players the owner fielded (in their FPL XI, not their own bench) who are NOT on
        # the pitch in the real match — benched now, or an unused sub once it is over.
        # These are the ones silently costing points, so surface them explicitly.
        starters_not_playing = [
            p["name"] for p in players
            if not p["on_bench"] and p["playing_status"] in ("benched (not on)", "did not play (unused sub)")
        ]

        result["my_players"] = {
            "entry_id": eid,
            "provisional_gameweek_points": provisional_points,
            "starters_not_playing": starters_not_playing,
            "note": "Provisional live points (starting XI x multiplier). 'playing_status' "
                    "reflects the REAL match (started/subbed on/benched), which FPL only "
                    "exposes once lineups are confirmed at kickoff; before that a benched "
                    "player is indistinguishable from one whose match has not started. FPL "
                    "applies bench auto-substitutions only after a player's match finishes.",
            "players": players,
        }

    return result













