from agents import RunContextWrapper, function_tool
from lib.core.registry import tool_ownership, TOOLS_BY_AGENT
from lib.cache import Cache, Ctx
from lib.memory import memory
from lib.scheduler import (
    store as scheduler_store,
    build_job,
    public_view as scheduler_public_view,
)
from lib.smart_device import SmartDevice, RGB, Mode
from lib.tuya_link import manager, SCAN_TIMEOUT
from lib.tools_utils import (
    fetch_fpl, fetch_fpl_bootstrap, index_bootstrap, resolve_gameweek, relevant_gameweek,
    describe_player, summarize_fixture_events, find_players_by_name)
from typing import List, Literal, Optional, Union, Annotated
import json
import asyncio
import os
import logging
import requests

DEVICES_PARAMS_PATH = "data/smart_device_data/smart_devices.json"
DEVICES_PREFERENCES_PATH = "data/smart_device_data/preferences.json"
MAPS_PARAMS_PATH = "data/maps_data/maps_memory.json"

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


# Cap on how many managers a league scan will fetch squads for, and how many of those
# picks requests run at once (politeness to the unofficial API). A personal mini-league
# fits well within the cap; huge public leagues are scanned from the top by rank.
_LEAGUE_SCAN_DEFAULT = 50
_LEAGUE_SCAN_CONCURRENCY = 8


async def _fetch_league_squads(league_id: int, gw: int, max_managers: int):
    """Fetch a league's managers (deduped by entry id, ordered by rank) and each one's
    squad for gameweek `gw`. Returns (league_name, [(manager_row, picks|None)]). Picks
    are None for a manager whose squad could not be fetched. Bounded concurrency."""
    managers: dict = {}  # entry id -> row (dedupe: standings pages can overlap on ties)
    league_name = None
    page = 1
    while len(managers) < max_managers:
        data = await fetch_fpl(f"leagues-classic/{league_id}/standings/?page_standings={page}")
        league_name = league_name or (data.get("league", {}) or {}).get("name")
        standings = data.get("standings", {}) or {}
        results = standings.get("results", []) or []
        if not results:
            break
        for row in results:
            managers.setdefault(row.get("entry"), row)
        if not standings.get("has_next"):
            break
        page += 1

    ordered = sorted(managers.values(), key=lambda r: r.get("rank") or 10**9)[:max_managers]

    sem = asyncio.Semaphore(_LEAGUE_SCAN_CONCURRENCY)

    async def _picks_for(row):
        async with sem:
            try:
                pd = await fetch_fpl(f"entry/{row.get('entry')}/event/{gw}/picks/")
                return row, pd.get("picks", [])
            except Exception:
                return row, None

    pairs = await asyncio.gather(*(_picks_for(r) for r in ordered))
    return league_name, pairs


@tool_ownership("fpl_agent")
@function_tool
async def who_owns_player_in_league(ctx: RunContextWrapper[Ctx],
                                    player: str,
                                    league_id: Optional[int] = None,
                                    gameweek: Optional[int] = None,
                                    max_managers: int = _LEAGUE_SCAN_DEFAULT) -> dict:
    """
    Description:
        Finds which managers in a Fantasy Premier League mini-league own a given player,
        and how many. Answers questions like "does anyone else in my league have Dalot"
        or "how many people have Haaland". It scans each manager's squad for the
        gameweek, so it also knows who has captained the player or has him on their bench.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    player: str
        The player's name (e.g. 'Haaland', 'Dalot', 'Bruno Fernandes'). Resolved against
        the FPL player list; if the name is ambiguous the tool returns the candidates so
        you can ask the user which one they meant.

    league_id: Optional[int] = None
        The classic league to scan. If omitted, the owner's default league from
        FPL_LEAGUE_ID is used. Use get_my_fpl_leagues to discover league ids.

    gameweek: Optional[int] = None
        The gameweek whose squads to check. If omitted, the current gameweek is used.

    max_managers: int = 50
        Safety cap on how many managers (from the top of the table by rank) to scan.

    Output:
        JSON object with the resolved player, the league, how many of the scanned
        managers own the player (with in-league ownership %) and the list of owners
        (manager, team name, rank, whether captained or benched by them).
    """
    lid = _fpl_league_id(league_id)
    if lid is None:
        return {
            "Error": "No FPL league id is configured.",
            "Tip": "Set FPL_LEAGUE_ID, pass league_id, or call get_my_fpl_leagues to find your league ids.",
        }
    logger.info(f"Scanning league {lid} for owners of '{player}'")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, gameweek, prefer="current")
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, _ = index_bootstrap(bootstrap)
    except Exception as e:
        logger.error("Error while preparing league ownership scan", exc_info=True)
        return {"Message": "Error while scanning the league", "Error": str(e)}

    matches = find_players_by_name(bootstrap, player)
    if not matches:
        return {"Error": f"No FPL player matched '{player}'."}
    if len(matches) > 1:
        return {
            "ambiguous_player": player,
            "candidates": [
                {"name": m.get("web_name"),
                 "full_name": f"{m.get('first_name','')} {m.get('second_name','')}".strip(),
                 "team": teams_by_id.get(m.get("team"), {}).get("short_name")}
                for m in matches[:10]
            ],
            "Tip": "Ask the user which player they meant, then call again with a more specific name.",
        }

    target = matches[0]
    target_id = target["id"]
    try:
        league_name, pairs = await _fetch_league_squads(lid, gw, max(1, max_managers))
    except Exception as e:
        logger.error("Error while fetching league squads", exc_info=True)
        return {"Message": "Error while fetching the league squads", "Error": str(e)}

    owners, scanned, failed = [], 0, 0
    for row, picks in pairs:
        if picks is None:
            failed += 1
            continue
        scanned += 1
        pick = next((p for p in picks if p.get("element") == target_id), None)
        if pick:
            owners.append({
                "manager": row.get("player_name"),
                "team_name": row.get("entry_name"),
                "rank": row.get("rank"),
                "is_captain": pick.get("is_captain", False),
                "on_their_bench": pick.get("position", 0) > 11,
            })

    owners.sort(key=lambda o: o.get("rank") or 10**9)
    return {
        "player": target.get("web_name"),
        "team": teams_by_id.get(target.get("team"), {}).get("short_name"),
        "league_id": lid,
        "league_name": league_name,
        "gameweek": gw,
        "managers_scanned": scanned,
        "owner_count": len(owners),
        "ownership_pct_in_league": round(100 * len(owners) / scanned, 1) if scanned else None,
        "owners": owners,
        "unavailable_squads": failed or None,
    }


@tool_ownership("fpl_agent")
@function_tool
async def get_league_ownership(ctx: RunContextWrapper[Ctx],
                               league_id: Optional[int] = None,
                               gameweek: Optional[int] = None,
                               top_n: int = 20,
                               max_managers: int = _LEAGUE_SCAN_DEFAULT) -> dict:
    """
    Description:
        Aggregates squad ownership across a Fantasy Premier League mini-league: which
        players are the most-owned within the league (effective in-league ownership) and
        how often each is captained. Use it for "what's popular in my league / most
        picked players / template" questions. To check one specific player instead, use
        who_owns_player_in_league.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    league_id: Optional[int] = None
        The classic league to scan. If omitted, the owner's default league from
        FPL_LEAGUE_ID is used.

    gameweek: Optional[int] = None
        The gameweek whose squads to aggregate. If omitted, the current gameweek is used.

    top_n: int = 20
        How many of the most-owned players to return.

    max_managers: int = 50
        Safety cap on how many managers (from the top of the table by rank) to scan.

    Output:
        JSON object with the league, how many managers were scanned and the most-owned
        players (name, team, position, how many managers own them, in-league ownership %
        and how many captained them).
    """
    lid = _fpl_league_id(league_id)
    if lid is None:
        return {
            "Error": "No FPL league id is configured.",
            "Tip": "Set FPL_LEAGUE_ID, pass league_id, or call get_my_fpl_leagues to find your league ids.",
        }
    logger.info(f"Aggregating ownership for league {lid}")
    try:
        bootstrap = await fetch_fpl_bootstrap()
        gw = resolve_gameweek(bootstrap, gameweek, prefer="current")
        if gw is None:
            return {"Error": "Could not determine a gameweek (no events in the FPL calendar)."}
        teams_by_id, elements_by_id, positions_by_id = index_bootstrap(bootstrap)
        league_name, pairs = await _fetch_league_squads(lid, gw, max(1, max_managers))
    except Exception as e:
        logger.error("Error while aggregating league ownership", exc_info=True)
        return {"Message": "Error while aggregating league ownership", "Error": str(e)}

    owned: dict = {}
    captained: dict = {}
    scanned = 0
    for _, picks in pairs:
        if picks is None:
            continue
        scanned += 1
        for p in picks:
            el = p.get("element")
            owned[el] = owned.get(el, 0) + 1
            if p.get("is_captain"):
                captained[el] = captained.get(el, 0) + 1

    ranked = sorted(owned.items(), key=lambda kv: kv[1], reverse=True)[: max(1, top_n)]
    top_owned = []
    for el, count in ranked:
        element = elements_by_id.get(el, {})
        top_owned.append({
            "name": element.get("web_name"),
            "team": teams_by_id.get(element.get("team"), {}).get("short_name"),
            "position": positions_by_id.get(element.get("element_type"), {}).get("singular_name_short"),
            "owned_by": count,
            "ownership_pct_in_league": round(100 * count / scanned, 1) if scanned else None,
            "captained_by": captained.get(el, 0),
        })

    return {
        "league_id": lid,
        "league_name": league_name,
        "gameweek": gw,
        "managers_scanned": scanned,
        "top_owned": top_owned,
    }

# ------- scheduler agent (proactive cron jobs / reminders) -------
#
# A scheduled job is a natural-language task for Jarvis plus WHEN to run it. When it
# fires, lib/scheduler_runner feeds the prompt back through the same coordinator with
# origin="system" and pushes the reply to the channel the job was created on. The
# delivery channel/target are derived server-side from the current conversation
# (ctx.conversation_id): the model never sees or handles the raw Messenger PSID.


def _channel_from_ctx(ctx: RunContextWrapper[Ctx]) -> tuple[str, Optional[str], str]:
    """Resolve (channel, target, conversation_id) for the current turn.

    'messenger:{psid}' -> deliver via Messenger to that PSID; anything else (e.g.
    'repl') -> the 'log' channel (no live socket to push to)."""
    conv = getattr(ctx.context, "conversation_id", None) or "repl"
    if conv.startswith("messenger:"):
        return "messenger", conv.split(":", 1)[1], conv
    return "log", None, conv


@tool_ownership("scheduler_agent")
@function_tool
async def create_scheduled_job(
    ctx: RunContextWrapper[Ctx],
    prompt: str,
    cron_expr: Optional[str] = None,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    until: Optional[str] = None,
    max_runs: Optional[int] = None,
) -> dict:
    """
    Description:
        Schedule Jarvis to run a task on its own in the future and message the user
        with the result — a reminder or a recurring proactive brief. When the job
        fires, `prompt` is executed by the full assistant (all subagents) exactly as
        if the user had asked it, and the reply is delivered back on the same channel
        the user is on now. Use this for "przypomnij mi za...", "codziennie o...",
        "co godzinę...", etc.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates (also carries the delivery channel).

    prompt: str
        The task to run when the job fires, written as a complete, self-contained
        instruction in the user's language — e.g. "Przypomnij mi o spotkaniu z Anią."
        or "Podaj prognozę pogody na dziś w Warszawie, krótki przegląd najważniejszych
        wiadomości, oraz sprawdź, czy dziś grają zawodnicy z mojego składu FPL."

    cron_expr: Optional[str]
        A standard 5-field cron expression for a RECURRING job, in the assistant's
        timezone (Europe/Warsaw). Examples: "0 8 * * *" (every day 08:00),
        "0 8 * * 1-5" (weekdays 08:00), "30 18 * * 1" (Mondays 18:30).

    run_at: Optional[str]
        ISO-8601 date-time for a ONE-OFF job at an absolute moment, e.g.
        "2026-08-23T09:00". Use the environment date/time to resolve "jutro", a
        weekday, etc. into a concrete value.

    delay_minutes: Optional[int]
        Minutes from now for a ONE-OFF job — the simplest way to do "za X" without
        date maths. "za dwie godziny" -> 120, "za pół godziny" -> 30.

        Provide EXACTLY ONE of cron_expr / run_at / delay_minutes.

    until: Optional[str]
        Optional end boundary for a recurring job (ISO date or date-time), inclusive.
        For "przez miesiąc" set it a month ahead. Ignored for one-off jobs.

    max_runs: Optional[int]
        Optional cap on how many times a recurring job runs before it stops.

    Output:
        JSON with the created job (id, human-readable schedule, next run time) — never
        the raw delivery target. On invalid input, an {"Error": ...} object; relay it
        so the user can correct the request.
    """
    channel, target, conv = _channel_from_ctx(ctx)
    try:
        job = build_job(
            prompt=prompt, channel=channel, target=target, conversation_id=conv,
            cron_expr=cron_expr, run_at=run_at, delay_minutes=delay_minutes,
            until=until, max_runs=max_runs,
        )
    except ValueError as e:
        return {"Error": str(e)}
    scheduler_store.add(job)
    logging.info(f"Scheduled job {job['id']} created ({job['kind']}, next {job['next_run_at']})")
    return {"scheduled": scheduler_public_view(job)}


@tool_ownership("scheduler_agent")
@function_tool
async def list_scheduled_jobs(ctx: RunContextWrapper[Ctx]) -> dict:
    """
    Description:
        List the user's active scheduled jobs (reminders and recurring tasks) for the
        current conversation, with their schedules and next run times. Use it to
        answer "co mam zaplanowane", or before deleting/updating a job to find its id.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates.

    Output:
        JSON with the list of active jobs (id, prompt, schedule, next run) and count.
    """
    _, _, conv = _channel_from_ctx(ctx)
    jobs = [scheduler_public_view(j) for j in scheduler_store.for_conversation(conv)]
    return {"jobs": jobs, "count": len(jobs)}


@tool_ownership("scheduler_agent")
@function_tool
async def delete_scheduled_job(ctx: RunContextWrapper[Ctx], job_id: str) -> dict:
    """
    Description:
        Cancel a scheduled job by its id (from list_scheduled_jobs). Use it for
        "odwołaj przypomnienie", "przestań mi wysyłać...", etc.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates.

    job_id: str
        The id of the job to cancel (e.g. 'job_1a2b3c4d').

    Output:
        JSON confirming deletion, or an error if the id was not found.
    """
    logging.info(f"Deleting scheduled job {job_id}")
    return {"deleted": job_id} if scheduler_store.delete(job_id) else {"Error": f"No scheduled job with id '{job_id}'"}


@tool_ownership("scheduler_agent")
@function_tool
async def update_scheduled_job(
    ctx: RunContextWrapper[Ctx],
    job_id: str,
    prompt: Optional[str] = None,
    until: Optional[str] = None,
    max_runs: Optional[int] = None,
) -> dict:
    """
    Description:
        Adjust an existing scheduled job (from list_scheduled_jobs): change what it
        says (prompt) or its limits (until / max_runs). To change the TIMING itself,
        delete the job and create a new one instead.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates.

    job_id: str
        The id of the job to update (e.g. 'job_1a2b3c4d').

    prompt: Optional[str]
        New task text, if changing it.

    until: Optional[str]
        New end boundary (ISO date or date-time), if changing it.

    max_runs: Optional[int]
        New cap on remaining runs, if changing it.

    Output:
        JSON with the updated job, or an error if the id was not found.
    """
    logging.info(f"Updating scheduled job {job_id}")
    job = scheduler_store.update(job_id, prompt=prompt, until=until, max_runs=max_runs)
    return {"updated": scheduler_public_view(job)} if job else {"Error": f"No scheduled job with id '{job_id}'"}













