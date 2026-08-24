import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="maps_agent")
def create_maps_agent():
    name = "maps_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are a Google Maps operator. Your task is to plan trips based on the traffic and the preferences\
            of the user.\
            You must always call get_maps_memory tool first before using any tools in order to understand user's preferences and be able \
            to understand user's requests in natural language. Without the output of this tool you may not be able to understand \
            user's requests."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Maps agent created")
    return agent
