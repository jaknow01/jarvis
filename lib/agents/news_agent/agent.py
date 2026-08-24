import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="news_agent")
def create_news_agent() -> FunctionAgent:
    name = "news_agent"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Summarizes current world and financial-market news.",
        system_prompt=(
            "You are a news reporter. Your task is to search the internet and use reputable sources to create \
            summaries of the events mentioned by the user."),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("News agent created")
    return agent
