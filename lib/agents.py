from lib.tools import (
    get_devices_state,
    turn_on_devices,
    get_route_details,
    get_maps_memory)
from lib.llm import LLM_BY_AGENT
from lib.tools import TOOLS_BY_AGENT
from agents import Agent

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
        # tools = TOOLS_BY_AGENT[name],
        tools = [
            # create_iot_agent().as_tool(
            #     tool_name="iot_operator",
            #     tool_description="Controls smart devices in a houshold."
            # ),
            create_maps_agent().as_tool(
                tool_name="maps_agent",
                tool_description="Controls access to maps and navigation. Can calculate routes."
            ),
            create_news_agent().as_tool(
                tool_name="news_agent",
                tool_description="Summarizes current political news."
            )
        ],
        model = model_settings["model_name"],
        model_settings = model_settings["settings"]
    )
    print("Utworzony koord")
    
    return agent

@agents_decorator(name="iot_operator")
def create_iot_agent():
    name = "iot_operator"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name = name,
        instructions=(
            "You can monitor and control all smart devices in a household (smart lights).\
            You can monitor and control all smart devices. Only call get_devices_state if the current state of devices is unknown.\
            otherwise you won't have necessary information about them to perform any action."
        ),
        tools = TOOLS_BY_AGENT[name],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )
    print("Utworzony iot")

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

    print("Utworzony google agent")
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

    print("Utworzony memory agent")
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

    print("Utworzony agent do wiadomości")
    return agent