from lib.tools import (
    get_devices_state,
    turn_on_devices,
    get_route_details,
    get_maps_memory,
    get_exchange_rate)
from lib.llm import LLM_BY_AGENT
from lib.tools import TOOLS_BY_AGENT
from agents import Agent
import logging

logger = logging.getLogger(__name__)
AGENTS: dict = {}

def agents_decorator(name: str):
    def wrapper(func):
        AGENTS[name] = func
        return func
    return wrapper

@agents_decorator(name="coordinator")
def create_coordinator_agent() -> Agent:
    name = "coordinator"
    model_settings = LLM_BY_AGENT[name]()

    # create_subagents() - trzeba stworzyc pomocnikow

    agent = Agent(
        name = name,
        instructions = ("Odpowiadaj wyłącznie po polsku. Zawsze zaczynaj odpowiedź od 'ABC'."),
        tools = [
            create_iot_agent().as_tool(
                tool_name="iot_operator",
                tool_description="Controls smart devices (lighting) in a houshold."
            ),
            create_news_agent().as_tool(
                tool_name="news_agent",
                tool_description="Summarizes current political news."
            ),
            create_weather_agent().as_tool(
                tool_name="weather_agent",
                tool_description="Checks current weather and weather forecast at a given location"
            create_finance_agent().as_tool(
                tool_name="finance_agent",
                tool_description="Retrieves and analyzes financial data."
            )
            # create_maps_agent().as_tool(
            #     tool_name="maps_agent",
            #     tool_description="Controls access to maps and navigation. Can calculate routes."
            # ),
            # create_news_agent().as_tool(
            #     tool_name="news_agent",
            #     tool_description="Summarizes current political news."
            # )
        ],
        model = model_settings["model_name"],
        model_settings = model_settings["settings"]
    )
    logger.info("Coordinator initiated")
    
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
            "You are responsible for managing the program's long term memory.\
            You can modify existing data, set reminders etc."
        ),
        tools = [],
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
            "You are a news reporter. Your task is to search twitter and create \
            summaries of the events mentioned by the user."),
        tools = [],
        model = model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    logger.info("News agent created")
    return agent