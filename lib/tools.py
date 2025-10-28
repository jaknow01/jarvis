from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, ColorMode
from typing import List
import json
import asyncio

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







