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
def create_coordinator_agent():
    name = "coordinator"
    model_settings = LLM_BY_AGENT[name]()

    agent = Agent(
        name = "Coordinator",
        instructions = ("Odpowiadaj wyłącznie po polsku. Zawsze zaczynaj odpowiedź od 'ABC'."),
        # tools = TOOLS_BY_AGENT[name],
        tools = [],
        model = model_settings["model_name"],
        model_settings = model_settings["settings"]
    )
    
    return agent