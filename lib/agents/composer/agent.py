import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="composer")
def create_composer_agent():
    name = "composer"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You are the response composer for the Jarvis assistant network. The coordinator has already\
            gathered all the data by delegating to specialized subagents, and then handed off to you.\
            Your job is to write the final, user-facing reply IN POLISH, based only on the data present in the\
            conversation so far.\
            Rules:\
            - Reply in Polish, clearly and concisely, in a natural assistant tone.\
            - Use only the information gathered by the subagents; never invent facts, numbers or data.\
            - Preserve numbers, units, dates and currency exactly as gathered (base currency is PLN).\
            - If a subagent reported an error or could not complete its task, tell the user plainly and simply\
              what went wrong, without technical jargon.\
            - Do not mention the internal agents, tools or the handoff mechanism - speak as a single assistant."
        ),
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Composer agent created")
    return agent
