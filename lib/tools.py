from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, ColorMode
from lib.tools_utils import simplify_directions_response
from typing import List, Literal, Optional, Union
import json
import asyncio
import googlemaps
import os
from datetime import datetime
import logging
from pyowm import OWM

TOOLS_BY_AGENT: dict[str: list[str]] = {}
DEVICES_PARAMS_PATH = "data/smart_device_data/smart_devices.json"
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

@tool_ownership("iot_operator")
@function_tool
async def get_devices_state(ctx: RunContextWrapper[Ctx]):
    """
    This tool is used to download neccessary data about all smart devices which is then
    used to establish connection and check their current status.
    """
    logger.info("Checking all available devices")
    with open(DEVICES_PARAMS_PATH, "r", encoding="utf-8") as f:
        list_of_jsons = json.load(f)

    configs = list_of_jsons["list_of_elements"]
    devices = []

    for c in configs:
        try:
            dev = await SmartDevice.create_from_json(c)
            devices.append(dev)
        except Exception as e:
            logging.error(f"Error creating device: {e}")

    states = await asyncio.gather(*(d.get_status() for d in devices))

    ctx.context.devices = states

    return states

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
        logger.error(f"Error while turning a device on {e}")
    return new_states

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

        result = simplify_directions_response(directions_result)
    except Exception as e:
        print(e)

    return result

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

# @tool_ownership("weather_agent")
# @function_tool
# async def weather_forecast(ctx: RunContextWrapper[Ctx],
#                            limit: int,
#                            city: str = "Warsaw"
#                            ) -> dict:
#     """
#     Description:
#         This tool is used to check a current weather forecast in a given location.
#         It can be either a short-term (min 3 hours) or a long-term (max 5 days) forecast
#         with different granularity (3h or daily intervals).

#     Parameters:
#     ctx : RunContextWrapper[Ctx]
#         Context in which the tool operates

 
#     limit: int
#         Maximum number of forecast data points (time steps) to retrieve.
#         Each data point represents a single forecasted moment - one 3-hour period
#         For example, setting `limit=8` with `interval='3h'` returns approximately 24 hours
#         of forecast data (8 x 3 hours), while `limit=5`.
#         If set to None, all available forecast points are returned.

#     city: str = "Warsaw"
#         Name of the city where the weather conditions are to be checked.
#         Unless specified otherwise by the user the default city is Warsaw.
#         Return the city name in nominative form (base form) — do not inflect or decline it.

#     Output:
#         JSON object with the weather forecast made according to specifications
#     """

#     owm_client = OWM(os.getenv("OPENWEATHER_API_KEY"))
#     owm_manager = owm_client.weather_manager()
#     interval ='3h'

#     logging.info(f"Getting weather forecast for {city} with {limit} x {interval} intervals")

#     try:
#         forecast = owm_manager.forecast_at_place(name=city, interval=interval, limit=limit)
#     except Exception as e:
#         logging.error(f"Couldnt get forecast for {city} with {limit} x {interval} intervals")
#         logging.error(e)
#         return {"message":f"Couldnt get forecast for {city} with {limit} x {interval} intervals",
#                 "exception":e}
    
#     return forecast













