from lib.tools import TOOLS_BY_AGENT
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
    
    return agent


@agents_decorator(name="iot_operator")
def create_iot_agent():
    name = "iot_operator"

    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name = name,
        tools = [],
        model=model_settings["model_name"],
        model_settings=model_settings["model_settings"]
    )

    return agent