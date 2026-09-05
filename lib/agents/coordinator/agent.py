import logging

from llama_index.core.agent.workflow import FunctionAgent

from lib.core import LLM_BY_AGENT, agents_decorator, agent_enabled, agent_as_tool
from lib.domain import memory

# Sibling factories, imported from their own feature packages (public surface). These
# packages never import the coordinator, so there is no import cycle.
from lib.agents.iot_operator import create_iot_agent
from lib.agents.weather_agent import create_weather_agent
from lib.agents.finance_agent import create_finance_agent
from lib.agents.maps_agent import create_maps_agent
from lib.agents.news_agent import create_news_agent
from lib.agents.memory_operator import create_memory_agent
from lib.agents.fpl_agent import create_fpl_agent
from lib.agents.scheduler_agent import create_scheduler_agent
from lib.agents.math_agent import create_math_agent

logger = logging.getLogger(__name__)


@agents_decorator(name="coordinator")
def create_coordinator_agent() -> FunctionAgent:
    name = "coordinator"
    llm = LLM_BY_AGENT[name]()

    instructions = (
        "You are a coordinator of a multiagent personal assistant network called Jarvis.\
        Your job is to gather the data needed to satisfy the user's request by delegating to specialized subagents.\
        In your possesion there are numerous specialized subagents which you can call as your tool\
        Each agent specializes in a narrow field that is of interest to the user.\
        These agents are equipped with various API connectors that allow them to obtain relevant, real-time data or perform certain actions\
        You should always call appropriate agent instead of relying on your built in knowledge.\
        Your tool-subagents can be run in parallel if the query requires multidomain knowledge.\
        You can also run the same tool-subagent multiple times in parallel if the query justifies it - it is especially helpful with news-agent.\
        \
        IMPORTANT: You do NOT write the final answer to the user yourself. Your only job is to gather everything needed\
        (including any error messages from failed tool calls) by calling the subagent tools. A separate composer step\
        writes the final user-facing reply from what you gathered, so do not craft the user's answer, and do not\
        apologize or converse - just gather. When you are done gathering, return a concise summary of the collected\
        results (and any errors) for the composer to turn into the reply. If a tool call fails, include what went wrong\
        so the composer can inform the user."
    )

    # Each subagent can be switched off via AGENT_<NAME>_ENABLED; a disabled agent is
    # simply not exposed to the coordinator as a tool.
    subagents = [
        ("iot_operator", create_iot_agent, "Controls smart devices (lighting) in a houshold."),
        ("weather_agent", create_weather_agent, "Checks current weather and weather forecast at a given location"),
        ("finance_agent", create_finance_agent, "Retrieves and analyzes financial data."),
        ("maps_agent", create_maps_agent, "Controls access to maps and navigation. Can calculate routes."),
        ("news_agent", create_news_agent, "Summarizes current world and financial-market news."),
        ("memory_operator", create_memory_agent, "Stores, retrieves and updates the user's long-term preferences and facts."),
        ("fpl_agent", create_fpl_agent, "Fantasy Premier League: upcoming fixtures, PL teams, the owner's squad and mini-league standings."),
        ("scheduler_agent", create_scheduler_agent, "Schedules Jarvis to act on its own later: one-off reminders, recurring proactive briefs, AND conditional watches that poll live data and notify ONLY when a condition becomes true ('daj znać, gdy...', 'powiadom, kiedy...'); plus listing/cancelling them."),
        ("math_agent", create_math_agent, "Performs exact calculations over numbers you provide: averages, min/max, sums, standard deviation, percentage change, linear trends and correlation. Use it whenever a request needs arithmetic/statistics over figures gathered from other agents (e.g. analyzing a financial or FPL time-series) instead of computing yourself."),
    ]

    if agent_enabled("scheduler_agent"):
        instructions += (
            "\n\nWhen the user wants something to happen later or on its own, delegate to the "
            "scheduler_agent. There are two shapes and you MUST NOT collapse the second into the first:\n"
            "- TIME-BASED ('przypomnij mi za...', 'codziennie o 8', 'co rano dawaj mi...'): a "
            "reminder/brief that fires on a clock. It will be re-run by you automatically, so phrase "
            "the job's prompt as a complete, self-contained request.\n"
            "- CONDITIONAL ('daj znać, GDY/KIEDY...', 'powiadom, gdy...', 'tylko jeśli...', 'sprawdzaj "
            "co minutę, czy...'): a watch that polls live data and notifies ONLY when a condition "
            "becomes true. Forward the user's request FAITHFULLY to the scheduler_agent — preserve the "
            "trigger condition, the 'only when' wording AND any stop/end condition (e.g. 'do końca "
            "meczu'). Do NOT rewrite it into a 'do X co minutę' procedure and do NOT ask for a frequent "
            "cron: turning a conditional alert into a per-minute cron floods the user and is a bug. Let "
            "the scheduler_agent pick the mechanism; for a watch it will confirm the condition with the "
            "user BEFORE arming, so pass its confirmation question/description straight back — do not "
            "report the watch as already set up until the user has confirmed."
        )

    if agent_enabled("memory_operator"):
        instructions += (
            "\n\nWhen the user states a durable preference or fact about themselves, store it via the "
            "memory_operator so future conversations stay personalized. Consult the memory_operator when "
            "you need details about the user's saved preferences."
        )
        profile = memory.summary()
        if profile:
            instructions += (
                "\n\nWhat you already know about the user (long-term memory — use it to personalize, "
                "and prefer it over asking the user again):\n" + profile
            )

    tools = []
    for sub_name, factory, description in subagents:
        if agent_enabled(sub_name):
            tools.append(agent_as_tool(factory(), tool_name=sub_name, tool_description=description))
        else:
            logger.info(f"Subagent '{sub_name}' disabled via env; not exposed to coordinator")

    agent = FunctionAgent(
        name=name,
        description="Routes user requests to specialized subagents and gathers their results.",
        system_prompt=instructions,
        tools=tools,
        llm=llm,
        allow_parallel_tool_calls=True,
    )
    logger.info(f"Coordinator initiated with {len(tools)} subagent tool(s)")

    return agent
