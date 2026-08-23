"""maps_agent tools: known-place aliases and route planning (Google Directions).
Registered into TOOLS_BY_AGENT["maps_agent"] via @tool_ownership.

The model works with place ALIASES only (Home, work, University, ...). The mapping of
an alias to its real street address is resolved server-side inside get_route_details,
so the user's actual home/work addresses are never handed to the LLM as a browsable
address book."""

import json
import logging
import os
from datetime import datetime
from typing import Literal, Optional, Union

import googlemaps
from agents import RunContextWrapper, function_tool

from lib.core import Ctx, tool_ownership
from lib.agents.maps_agent.directions import (
    normalize_departure_time,
    simplify_directions_response,
)

logger = logging.getLogger(__name__)

MAPS_PARAMS_PATH = "data/maps_data/maps_memory.json"


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
