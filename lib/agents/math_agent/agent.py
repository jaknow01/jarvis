import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, TOOLS_BY_AGENT, agents_decorator

logger = logging.getLogger(__name__)


@agents_decorator(name="math_agent")
def create_math_agent() -> FunctionAgent:
    name = "math_agent"
    llm = LLM_BY_AGENT[name]()

    agent = FunctionAgent(
        name=name,
        description="Performs deterministic calculations over sets of numbers.",
        system_prompt=(
            "You are a calculator. You perform exact numeric calculations using your tools "
            "and never do arithmetic in your head. Given a set of numbers you can compute "
            "summary statistics (average, median, min, max, sum, standard deviation), the "
            "percentage change between two values, a linear trend over an ordered sequence, "
            "and the correlation between two series. Always pick the tool that matches the "
            "request, pass the numbers through verbatim, and report the tool's results."
        ),
        tools=TOOLS_BY_AGENT[name],
        llm=llm,
    )

    logger.info("Math agent created")
    return agent
