from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, ColorMode
from typing import List, Literal, Optional
import json
import asyncio
import googlemaps
import os
from datetime import datetime

TOOLS_BY_AGENT: dict[str: list[str]] = {}
DEVICES_PARAMS_PATH = "data/smart_device_data/smart_devices.json"


# def tool_ownership(agent_name: str):
#     def wrapper(function_tool):
#         if agent_name in TOOLS_BY_AGENT:
#             TOOLS_BY_AGENT[agent_name].append(function_tool)
#         else:
#             TOOLS_BY_AGENT[agent_name] = [func]
#         return func
#     return wrapper


@function_tool
# @tool_ownership("iot_operator")
async def get_devices_state(ctx: RunContextWrapper[Ctx]):
    """
    This tool is used to download neccessary data about all smart devices which is then
    used to establish connection and check their current status.
    """
    print("Sprawdzam dostępne urządzenia")
    with open(DEVICES_PARAMS_PATH, "r", encoding="utf-8") as f:
        list_of_jsons = json.load(f)

    configs = list_of_jsons["list_of_elements"]
    devices = []

    for c in configs:
        try:
            dev = await SmartDevice.create_from_json(c)
            devices.append(dev)
        except Exception as e:
            print(f"Error creating device: {e}")


    states = await asyncio.gather(*(d.get_status() for d in devices))

    ctx.context.devices = states

    return states

@function_tool(strict_mode=False)
# @tool_ownership("iot_operator")
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
    print("Uruchamiam drugi tool")


    try:
        await asyncio.gather(*(dev.turn_on() for dev in devices))

        new_states = await asyncio.gather(*(dev.get_status() for dev in devices))
        
    except Exception as e:
        print(e)
    return new_states

@function_tool
async def get_route_details(ctx: RunContextWrapper[Ctx],
                            origin: str,
                            destination: str,
                            transport_mode: str,
                            departure_time: Optional[str] = "now",
                            #arrival_time: Optional[str] = None
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
        The starting point of the journey. Most of the time this will be a specific adress but it can also
        be a specific bus/metro/train stop.

    destination: str
        The end of the journey. Most of the time this will be a specific adress but it can also
        be a specific bus/metro/train stop.

    transport_mode: str
        User's preffered mode of communication such as 'car', 'transit' etc.

    departure_time: Optional[str] = "now"
        Time at which user wishes to leave. By default is set to 'now'.

    Output:
        This function returns a json file with all of the steps of the most optimal route from origin to destination
        along with all transfers if necessary. Should an error occurr this function will return a json with the
        proper error message.
    
    Note:
        The user is a fast-walker therefore you should assume that all distances that require traveling on foot will
        be covered in 1.25x faster than the navigation data suggests.
    """

    gmaps_client = googlemaps.Client(os.getenv("GOOGLE_MAPS_API_KEY"))

    directions_result = gmaps.directions(
        origin=origin,
        destination=destination,
        mode=transport_mode,       # driving, walking, bicycling, transit
        departure_time=departure_time
    )

    return directions_result













