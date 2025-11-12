from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, ColorMode
from lib.tools_utils import simplify_directions_response, get_forecast validate_currency_code
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
    """
    logger.info("Checking all available devices")
    with open(DEVICES_PARAMS_PATH, "r", encoding="utf-8") as f:
        list_of_jsons = json.load(f)

    print("Wczytuje preferencje użytkownika")
    with open(DEVICES_PREFERENCES_PATH, "r", encoding="utf-8") as f:
        preferences = json.load(f)

    ctx.context.devices_preferences = preferences

    configs = list_of_jsons["list_of_elements"]
    devices = []

    for c in configs:
        try:
            dev = await SmartDevice.create_from_json(c)
            devices.append(dev)
        except Exception as e:
            logging.error(f"Error creating device: {e}")

    states = await asyncio.gather(*(d.get_status() for d in devices))
    ctx.context.devices_states = states

    devices_dict = {d.name : d for d in devices}
    ctx.context.devices = devices_dict

    return {"states" : states, "known_user_preferences": preferences}

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def get_one_device_status(ctx: RunContextWrapper[Ctx], device: SmartDevice) -> dict:
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
    
    devices : SmartDevice
        Devices that should have its status checked

    Output:
        State of the given device
    """

    print("Sprawdzam stan jednego urzadzenia")
    state = await device.get_status()

    ctx.context.devices_states[device.get_name()] = state
    return state

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def turn_on_devices(ctx: RunContextWrapper[Ctx], devices: List[SmartDevice]):
    """
    Description:
    This tool is used to turn on all mentioned devices.

    Note:
    This tool should always be preceded by the usage of get_devices_state tool.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates
    
    devices : List[SmartDevice]
        List of all devices that should be turned on based on the user's request

    Output:
    This tool returns the new states of the affected devices
    """
    logger.info("Turning on a device")
    try:
        await asyncio.gather(*(dev.turn_on() for dev in devices))

        new_states = await asyncio.gather(*(dev.get_status() for dev in devices))
        
    except Exception as e:
        logging.error(f"Error while turning devices off {e}"}"

    return new_states

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def turn_off_devices(ctx: RunContextWrapper[Ctx], devices: List[SmartDevice]):
    """
    Description:
    This tool is used to turn off all mentioned devices.

    Note:
    This tool should always be preceded by the usage of get_devices_state tool.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates
    
    devices : List[SmartDevice]
        List of all devices that should be turned on based on the user's request

    Output:
    This tool returns the new states of the affected devices
    """
    print("Wyłączam urządzenia")

    try:
        await asyncio.gather(*(dev.turn_off() for dev in devices))

        new_states = await asyncio.gather(*(dev.get_status() for dev in devices))
        
    except Exception as e:
        logger.error(f"Error while turning a device on {e}")
    return new_states

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def change_lighting_mode(ctx: RunContextWrapper[Ctx], device: SmartDevice, new_mode: Mode) -> dict:
    """
    Description:
    This tool is used to change the lighting mode of a given smart device. Lighting mode can either
    be set to white or colour mode. When in colour mode various rgb settings can be applied to the
    device. When in white mode the lighting temperature can be adjusted.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device: SmartDevice
        The device that is to be affected by the mode change

    new_mode: Mode
        The mode that will be applied to the chosen device
    """

    print(f"Zmieniam tryb na {new_mode.mode}")
    await device.change_mode(new_mode)

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def change_color(ctx: RunContextWrapper[Ctx], device: SmartDevice, new_color: RGB) -> dict:
    """
    Description:
    This tool is used to change the colour of the given smart device.
    In order to set a new RGB value device must be in 'colour' lighting mode.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device: SmartDevice
        The device that is to be affected by the color change
        Note: this device must be in 'colour' lighting mode in order for the change to be possible

    new_color: RGB
        The new color that the device will be set to as an RGB value.
        RGB values are integers from 0 to 255 where R = red, G = green, B = blue

    Output:
        This tool returns short information whether the attempt was successful
    """

    print(f"Zmieniam kolor na {new_color.R} {new_color.G} {new_color.B}")
    task_status = await device.change_color(new_color)
    return task_status

@tool_ownership("iot_operator")
@function_tool(strict_mode=False)
async def change_light_temperature(ctx: RunContextWrapper, device: SmartDevice, new_temp: Annotated[int, "range 0-1000"]) -> dict:
    """
    Description:
    This tool is used to change the colour temperature of the given device.
    In order to set a new color temperature the device must be in 'white' lighting mode.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    device: SmartDevice
        The device that is to be affected by the lighting temperature change
        Note: this device must be in 'white' lighting mode in order for the change to be possible

    new_temp: Annotated[int, "range 0-1000"]
        This parameter controls the temperature value where 0 is the brightest and 1000 the coldest

    Output:
        This tool returns short information whether the attempt was successful
    """
    print("Zmieniam temperature")
    task_status = await device.change_temperature(new_temp)
    return task_status

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
    """

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
    
    









