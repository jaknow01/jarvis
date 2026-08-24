import logging

from agents import Agent

from lib.core import LLM_BY_AGENT, agents_decorator, agent_enabled
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
from lib.agents.composer import create_composer_agent

logger = logging.getLogger(__name__)


@agents_decorator(name="coordinator")
def create_coordinator_agent() -> Agent:
    name = "coordinator"
    model_settings = LLM_BY_AGENT[name]()

    # create_subagents() - trzeba stworzyc pomocnikow

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
        IMPORTANT: You do NOT write the final answer to the user yourself. Once you have gathered everything needed\
        (including any error messages from failed tool calls), you MUST always hand off to the 'composer' agent, which\
        writes the final user-facing reply. Do not paraphrase or summarize the results yourself - just gather and hand off.\
        If a tool call fails, still hand off to the composer and let it inform the user; pass along what went wrong."
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
        ("scheduler_agent", create_scheduler_agent, "Schedules Jarvis to act on its own later: one-off reminders and recurring proactive briefs, plus listing/cancelling them."),
    ]

    if agent_enabled("scheduler_agent"):
        instructions += (
            "\n\nWhen the user asks to be reminded later or wants something done on a recurring "
            "schedule ('przypomnij mi za...', 'codziennie o...', 'co rano dawaj mi...'), delegate to "
            "the scheduler_agent to create/list/cancel scheduled jobs. The scheduled task itself will "
            "later be run by you again automatically, so phrase the job's prompt as a complete request."
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
            tools.append(factory().as_tool(tool_name=sub_name, tool_description=description))
        else:
            logger.info(f"Subagent '{sub_name}' disabled via env; not exposed to coordinator")

    agent = Agent(
        name = name,
        instructions = instructions,
        tools = tools,
        handoffs = [create_composer_agent()],
        model = model_settings["model_name"],
        model_settings = model_settings["settings"]
    )
    logger.info(f"Coordinator initiated with {len(tools)} subagent tool(s)")

    return agent
