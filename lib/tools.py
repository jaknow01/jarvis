from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, ColorMode
from lib.tools_utils import simplify_directions_response, validate_currency_code
from typing import List, Literal, Optional, Union
import json
import asyncio
import googlemaps
import os
from datetime import datetime
import logging
import requests
from requests import HTTPError

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
    
    









