from lib.tools import get_devices_state, turn_on_devices, get_route_details
from lib.llm import LLM_BY_AGENT
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
        tools = [get_devices_state, turn_on_devices],
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
            of the user."
        ),
        tools = [get_route_details],
        model=model_settings["model_name"],
        model_settings=model_settings["settings"]
    )

    print("Utworzony google agent")
    return agent