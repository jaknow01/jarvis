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
            Make sure to use all necessary tools.\
            For the CURRENT value of an instrument use get_exchange_rate / get_stock_quote. Ticker symbols\
            are not guessable: unless you are certain of one, find it with search_ticker first. For how a\
            value CHANGED OVER TIME (a trend, a period comparison, growth over weeks/months/a year)\
            use get_price_history, which reads Jarvis's own daily time-series database (FX vs PLN,\
            the S&P 500 and its PLN equivalent, gold and silver)."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("Finance agent created")
    return agent
