import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="finance_agent")
def create_finance_agent():
    name = "finance_agent"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are responsible for retrieving and analyzing financial data based on user's requests.\
            Make sure to use all necessary tools."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Finance agent created")
    return agent
