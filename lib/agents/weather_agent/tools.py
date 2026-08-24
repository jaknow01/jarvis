"""weather_agent tools: current conditions (OpenWeather), date/time, and forecast
(Open-Meteo). Registered into TOOLS_BY_AGENT["weather_agent"] via @tool_ownership."""

import asyncio
import logging
import os
from datetime import datetime
from typing import Literal

import requests
from requests import HTTPError
from pyowm import OWM
from agents import RunContextWrapper, function_tool

from lib.core import Ctx, tool_ownership
from lib.agents.weather_agent.forecast import get_forecast

logger = logging.getLogger(__name__)


@tool_ownership("weather_agent")
@function_tool
async def current_weather(ctx: RunContextWrapper[Ctx], city: str = "Warsaw") -> dict:
    """
    Description:
        This tool is used to get the current weather conditions in a specified city.

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    city: str = "Warsaw"
        Name of the city where the weather conditions are to be checked.
        Unless specified otherwise by the user the default city is Warsaw.
        Return the city name in nominative form (base form) — do not inflect or decline it.

    Output:
        JSON object with current weather conditions in a specified place.
    """

    owm_client = OWM(os.getenv("OPENWEATHER_API_KEY"))
    owm_manager = owm_client.weather_manager()

    logging.info(f"Getting weather at {city}")

    try:
        current_weather = owm_manager.weather_at_place(city)
    except Exception as e:
        logging.error(f"Couldnt get weather at {city}")
        return {"message": f"Couldnt get weather at {city}",
                "exception": e}

    return current_weather

@tool_ownership("weather_agent")
@function_tool
async def get_current_date_and_time(ctx: RunContextWrapper[Ctx]) -> dict:
    """
    Description:
        This tool is used to obtain today's date and current time. It is neccessary
        to use it before getting the weather forecasts otherwise agent will not be
        able to process user's request properly when it comes to dates and time.

    Output:
        JSON object with the current local date, time, weekday and ISO 8601 timestamp.
    """
    now = datetime.now()
    logger.info("Providing current date and time")
    now_params = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "iso": now.isoformat(),
    }
    # Stored on the context so weather_forecast can enforce that this ran first.
    ctx.context.time_date_now = now_params
    return now_params

@tool_ownership("weather_agent")
@function_tool
async def weather_forecast(ctx: RunContextWrapper[Ctx],
                           forecast_days: Literal["1", "3", "7"],
                           forecast_type: Literal["hourly", "daily"],
                           city: str = "Warsaw"
                           ) -> dict:
    """
    Important:

    Description:
        This tool is used to check a current weather forecast in a given location.
        It can be either a short-term (min 3 hours) or a long-term (max 5 days) forecast
        with different granularity (3h or daily intervals).

    Parameters:
    ctx : RunContextWrapper[Ctx]
        Context in which the tool operates

    forecast_days: Literal["1", "3", "7"]
        How long into the future should the forecast reach measured in days.

    forecast_type: Literal["hourly", "daily"]
        Time intervals in which the forecast will be divided. When asking for a short-term forecast
        more granular data obtained with 'hourly' may be more optimal wheras for long-term forecast
        it usually is better to provide 'daily' intervals.

    city: str = "Warsaw"
        Name of the city where the weather conditions are to be checked.
        Unless specified otherwise by the user the default city is Warsaw. The city name should be in polish.
        Return the city name in nominative form (base form) — do not inflect or decline it.

    Output:
        JSON object with the weather forecast made according to specifications
    """
    if not ctx.context.time_date_now:
        return {
            "Message": "You don't know the current date, weekday and time - the forecast could be inaccurate.",
            "Tip": "Run the get_current_date_and_time tool first, then call this tool again.",
        }

    multiple_results = False
    geolocation_url = f"https://nominatim.openstreetmap.org/search?q={city}&format=json"

    headers = {
        "User-Agent":"Jarvis (lkc86484@laoia.com)",
        "Accept":"application/json"
    }

    logging.info(f"Starting geolocation for {city}")
    try:
        geolocation = requests.get(geolocation_url, headers=headers)
        geolocation = geolocation.json()
        logging.info("Geolocation obtained")
    except HTTPError as e:
        logging.error(f"Couldnt geolocate {city} - issue with API")
        return {"message" : f"Couldnt geolocate this location {city}",
                "status_code" : e}

    output = [
        {"name" : result["display_name"], "long" : result["lon"], "lat" : result["lat"]}
        for result in geolocation
    ]

    if len(output) > 1:
        logging.info("Found more than one geolocation")
        multiple_results = True
        if len(output) > 3:
            logging.info("Found more than three geolocations")
            output = output[:3]

    logging.info(f"Getting {forecast_type} in {forecast_days} intervals")
    tasks = [
        get_forecast(p, forecast_days, forecast_type)
        for p in output
    ]

    forecasts = await asyncio.gather(*tasks)

    result = {
        "Message" : f"Successfully obtained weather forecasts for {city}",
        "Forecast" : forecasts
    }

    if multiple_results:
        location_names = ",".join([l["name"] for l in output])
        result["Note"] = f"Multiple geolocations have been found for {city}.\
            If they are not actually the same city listed out multiple times inform the user about this.\
                Location names: {location_names}"

    return result
