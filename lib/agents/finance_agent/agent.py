import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="finance_agent")
def create_finance_agent() -> FunctionAgent:
    name = "finance_agent"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Retrieves and analyzes financial data.",
        system_prompt=(
            "You are responsible for retrieving and analyzing financial data based on user's requests.\
            Make sure to use all necessary tools."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("Finance agent created")
    return agent
