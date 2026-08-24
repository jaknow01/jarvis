"""iot_operator tools: smart-lighting control (Tuya) — state, on/off, mode, colour and
temperature. Registered into TOOLS_BY_AGENT["iot_operator"] via @tool_ownership.

The model refers to devices only by their human-readable NAME; ip / local_key / dev_id
never reach the LLM (resolved back to a real SmartDevice server-side here)."""

from agents import RunContextWrapper, function_tool
from typing import List, Annotated
import json
import asyncio
import logging

from lib.core import Ctx, tool_ownership
from lib.agents.iot_operator.device import SmartDevice, RGB, Mode
from lib.agents.iot_operator.tuya_link import manager, SCAN_TIMEOUT

DEVICES_PARAMS_PATH = "data/smart_device_data/smart_devices.json"
DEVICES_PREFERENCES_PATH = "data/smart_device_data/preferences.json"

logger = logging.getLogger(__name__)

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

