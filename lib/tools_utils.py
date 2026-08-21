import json
import openmeteo_requests
import requests_cache
from retry_requests import retry
from typing import Literal, Optional, Union
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo
import logging
import pycountry

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

async def get_forecast(params: dict,
                       forecast_days: Literal["1", "3", "7"],
                       forecast_type: Literal["hourly", "daily"]
                       ) -> dict:
    
    cache_session = requests_cache.CachedSession('.cache', expire_after = 3600)
    retry_session = retry(cache_session, retries = 5, backoff_factor = 0.2)
    openmeteo = openmeteo_requests.Client(session = retry_session)

    forecast_url = "https://api.open-meteo.com/v1/forecast"

    if forecast_type == "daily":
        type_params = ["temperature_2m_max", "temperature_2m_min", "weather_code", "sunrise", "sunset", "uv_index_max", "rain_sum", "snowfall_sum"]
    else:
        type_params = ["temperature_2m", "apparent_temperature", "weather_code", "precipitation"]

    request_params = {
        "latitude": params["lat"],
        "longitude": params["long"],
        forecast_type: type_params,
        "forecast_days": forecast_days
    }

    try:
        response = openmeteo.weather_api(url=forecast_url, params=request_params)
        response = response[0]
    except Exception as e:
        logging.error("Error while getting the forecast")
        return []

    data = response.Daily() if forecast_type == "daily" else response.Hourly()

    result = {}
    for i, variable in enumerate(type_params):
        value = data.Variables(i).ValuesAsNumpy()
        if hasattr(value, "tolist"):
            result[variable] = value.tolist()
        else:
            result[variable] = [value] 

    start_time = datetime.fromtimestamp(data.Time())
    end_time = datetime.fromtimestamp(data.TimeEnd())
    interval = timedelta(seconds=data.Interval())

    timestamps = []
    current = start_time
    while current < end_time:
        timestamps.append(current.isoformat())
        current += interval

    result["time"] = timestamps

    result["meta"] = {
        "lat": response.Latitude(),
        "long": response.Longitude(),
        "elevation": response.Elevation(),
        "utc_offset": response.UtcOffsetSeconds()
    }

    logging.info("Forecasts obtained successfully")

    return result
def validate_currency_code(code: str) -> bool:
    return pycountry.currencies.get(alpha_3 = code) is not None
