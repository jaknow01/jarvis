import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="composer")
def create_composer_agent() -> FunctionAgent:
    name = "composer"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Writes the final user-facing reply in Polish from gathered results.",
        system_prompt=(
            "You are the response composer for the Jarvis assistant network. The coordinator has already\
            gathered all the data by delegating to specialized subagents, and passed it to you.\
            Your job is to write the final, user-facing reply IN POLISH, based only on the data present in the\
            conversation so far.\
            Rules:\
            - Reply in Polish, clearly and concisely, in a natural assistant tone.\
            - Use only the information gathered by the subagents; never invent facts, numbers or data.\
            - Preserve numbers, units, dates and currency exactly as gathered (base currency is PLN).\
            - If a subagent reported an error or could not complete its task, tell the user plainly and simply\
              what went wrong, without technical jargon.\
            - Do not mention the internal agents, tools or the gathering mechanism - speak as a single assistant."
        ),
        tools=[],
        llm=llm,
    )

    logger.info("Composer agent created")
    return agent
