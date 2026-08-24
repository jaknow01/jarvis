import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="news_agent")
def create_news_agent():
    name = "news_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are a news reporter. Your task is to search the internet and use reputable sources to create \
            summaries of the events mentioned by the user."),
        tools = TOOLS_BY_AGENT[name],
        model = model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("News agent created")
    return agent
