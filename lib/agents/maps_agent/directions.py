"""maps_agent private helpers: departure-time normalization and Google Directions
response simplification. Moved here from the shared lib/tools_utils grab-bag — single
consumer (get_route_details)."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Union

logger = logging.getLogger(__name__)

# The owner is in Warsaw; naive/local times from the model are interpreted here.
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def _to_epoch(dt: datetime) -> int:
    """Correct POSIX timestamp for ``dt``, treating a naive value as Warsaw local.

    We compute the epoch ourselves rather than handing a datetime to the googlemaps
    client, because its ``convert.time()`` does ``calendar.timegm(dt.timetuple())`` —
    which drops tzinfo and reads the wall clock as UTC, shifting Warsaw times by the
    local offset.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WARSAW_TZ)
    return int(dt.timestamp())


def _parse_datetime_string(s: str) -> Optional[datetime]:
    """Best-effort parse of a model-supplied time into a datetime.

    Handles full ISO datetimes / dates (``fromisoformat``) and bare wall-clock
    times like ``"8:00"`` / ``"08:00:00"`` (combined with today's Warsaw date).
    Returns ``None`` when nothing matches.
    """
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.strptime(s, fmt).time()
            today = datetime.now(WARSAW_TZ).date()
            return datetime.combine(today, t)
        except ValueError:
            continue
    return None


def normalize_departure_time(value: Optional[Union[str, datetime, int, float]]) -> Union[str, int]:
    """Normalize a ``departure_time`` for the Google Directions API.

    Google accepts only the literal ``"now"`` or an integer Unix timestamp; any other
    string (ISO datetime, ``"8:00"``, …) returns HTTP 400. This coerces the model's
    input into one of those two forms, interpreting naive/local times as Warsaw, and
    falls back to ``"now"`` on anything unparseable rather than letting the call 400.
    """
    if value is None:
        return "now"
    if isinstance(value, datetime):
        return _to_epoch(value)
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s or s.lower() == "now":
        return "now"
    dt = _parse_datetime_string(s)
    if dt is None:
        logger.warning("Could not parse departure_time %r; falling back to 'now'", value)
        return "now"
    return _to_epoch(dt)


async def simplify_directions_response(data):
    routes_summary = []

    for route in data:
        for leg in route.get("legs", []):
            summary = {
                "start_address": leg.get("start_address"),
                "end_address": leg.get("end_address"),
                "departure_time": leg.get("departure_time", {}).get("text"),
                "arrival_time": leg.get("arrival_time", {}).get("text"),
                "total_distance": leg.get("distance", {}).get("text"),
                "total_duration": leg.get("duration", {}).get("text"),
                "steps": []
            }

            for step in leg.get("steps", []):
                step_info = {
                    "instruction": step.get("html_instructions"),
                    "distance": step.get("distance", {}).get("text"),
                    "duration": step.get("duration", {}).get("text"),
                    "travel_mode": step.get("travel_mode"),
                }

                if "transit_details" in step:
                    transit = step["transit_details"]
                    step_info["transit"] = {
                        "line": transit["line"].get("short_name") or transit["line"].get("name"),
                        "vehicle": transit["line"]["vehicle"].get("name"),
                        "departure_stop": transit["departure_stop"]["name"],
                        "arrival_stop": transit["arrival_stop"]["name"],
                        "num_stops": transit.get("num_stops")
                    }

                summary["steps"].append(step_info)

            routes_summary.append(summary)

    return routes_summary
