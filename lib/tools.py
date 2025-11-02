from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, Mode
from lib.tools_utils import simplify_directions_response
from typing import List, Literal, Optional, Union, Annotated
import json
import asyncio
import googlemaps
import os
from datetime import datetime

TOOLS_BY_AGENT: dict[str: list[str]] = {}
DEVICES_PARAMS_PATH = "data/smart_device_data/smart_devices.json"
DEVICES_PREFERENCES_PATH = "data/smart_device_data/preferences.json"
MAPS_PARAMS_PATH = "data/maps_data/maps_memory.json"


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
    print("Sprawdzam dostępne urządzenia")
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
            print(f"Error creating device: {e}")


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
    print("Włączam urządzenia")

    try:
        await asyncio.gather(*(dev.turn_on() for dev in devices))

        new_states = await asyncio.gather(*(dev.get_status() for dev in devices))
        
    except Exception as e:
        print(e)

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
        print(e)
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
    print("Sprawdzam znane adresy")
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

    print("Rozpoczynam planowanie trasy")

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













