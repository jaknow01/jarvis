from agents import RunContextWrapper, function_tool
from lib.cache import Cache, Ctx
from lib.smart_device import SmartDevice, RGB, ColorMode
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

    with open(DEVICES_PARAMS_PATH, "r", encoding="utf-8") as f:
        list_of_jsons = json.load(f)

    configs = list_of_jsons["list_of_elements"]
    devices = []

    for c in configs:
        dev = await SmartDevice.from_json(c)
        devices.append(dev)

    states = await asyncio.gather(*(d.get_status() for d in devices))


    ctx.context.devices = states

    return states








