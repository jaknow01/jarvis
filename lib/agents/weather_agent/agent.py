import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="weather_agent")
def create_weather_agent():
    name="weather_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You can check current weather conditions as well as a short-term forecast.\
            Before answering ANY question that involves a date or a relative day\
            ('today', 'tomorrow', 'jutro', 'weekend', a weekday name, etc.), you MUST first call\
            get_current_date_and_time to establish today's date, weekday and time. Only then map the\
            user's relative day to a concrete calendar date and call weather_forecast. Never assume\
            what 'today' or 'tomorrow' is - always resolve it from get_current_date_and_time first,\
            and make sure the date you report back to the user matches that resolution."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Weather agent created")
    return agent
