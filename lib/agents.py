from lib.tools import get_devices_state, turn_on_devices
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
            create_iot_agent().as_tool(
                tool_name="iot_operator",
                tool_description="Controls smart devices in a houshold."
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