from lib.tools import (
    get_devices_state,
    turn_on_devices,
    get_route_details,
    get_maps_memory,
    get_exchange_rate)
from lib.llm import LLM_BY_AGENT
from lib.tools import TOOLS_BY_AGENT
from lib.memory import memory
from agents import Agent
import logging
import os

logger = logging.getLogger(__name__)
AGENTS: dict = {}

def agents_decorator(name: str):
    def wrapper(func):
        AGENTS[name] = func
        return func
    return wrapper

def agent_enabled(name: str) -> bool:
    """Whether a subagent is exposed to the coordinator. Controlled by the env var
    AGENT_<NAME>_ENABLED — enabled by default; set to a falsy value (0/false/no/off)
    to hide it as a coordinator tool. Read at coordinator-build time."""
    raw = os.getenv(f"AGENT_{name.upper()}_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")

@agents_decorator(name="coordinator")
def create_coordinator_agent() -> Agent:
    name = "coordinator"
    model_settings = LLM_BY_AGENT[name]()

    # create_subagents() - trzeba stworzyc pomocnikow

    instructions = (
        "You are a coordinator of a multiagent personal assistant network called Jarvis.\
        Your main goal is to satisfy user's demands and give him appropriate answers.\
        In your possesion there are numerous specialized subagents which you can call as your tool\
        Each agent specializes in a narrow field that is of interest to the user.\
        These agents are equipped with various API connectors that allow them to obtain relevant, real-time data or perform certain actions\
        You should always call appropriate agent instead of relying on your built in knowledge.\
        Your tool-subagents can be run in parallel if the query requires multidomain knowledge.\
        You can also run the same tool-subagent multiple times in parallel if the query justifies it - it is especially helpful with news-agent.\
        If you encounter any bugs or error messages in your tool calls you should inform the user immediately. Cleanly and plainly inform him what the issue is."
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
    ]

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
        model = model_settings["model_name"],
        model_settings = model_settings["settings"]
    )
    logger.info(f"Coordinator initiated with {len(tools)} subagent tool(s)")

    return agent

@agents_decorator(name="iot_operator")
def create_iot_agent():
    name = "iot_operator"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name = name,
        instructions=(
            "You are an operator of all smart devices (lighting) in the household.\
            Your task is to manipulate devices' states based on the user's preferences. \
            You must start your tool run by utilizing get_devices_state in order to initially access\
            the device database and establish connection, as well as to understand user's preferences\
            that are stored in long term memory database." \
            "Always try to run as many necessary tools as possible in paralel."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )
    logger.info("IoT agent initiated")

    return agent

@agents_decorator(name="maps_agent")
def create_maps_agent():
    name = "maps_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You are a Google Maps operator. Your task is to plan trips based on the traffic and the preferences\
            of the user.\
            You must always call get_maps_memory tool first before using any tools in order to understand user's preferences and be able \
            to understand user's requests in natural language. Without the output of this tool you may not be able to understand \
            user's requests."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Maps agent created")
    return agent
          
@agents_decorator(name="weather_agent")
def create_weather_agent():
    name="weather_agent"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions=(
            "You can check current weather conditions as well as a short-term forecast"
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Weather agent created")
    return agent

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

@agents_decorator(name="memory_operator")
def create_memory_agent():
    name = "memory_operator"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name=name,
        instructions = (
            "You manage the user's long-term memory: durable preferences, facts, habits, interests and routines.\
            Use get_memory to look things up, save_memory to store a new durable preference or fact\
            (set source='user' when the user stated it explicitly, 'inferred' when you concluded it),\
            update_memory to correct an existing entry, and delete_memory to remove one.\
            Store concise, self-contained statements in natural language. Suggested categories:\
            preferences, facts, habits, interests, routines. Never store transient or one-off details,\
            secrets, or credentials."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("Memory agent created")
    return agent

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