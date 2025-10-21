from lib.tools import TOOLS_BY_AGENT
from lib.llm import LLM_BY_AGENT
from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_agent

AGENTS: dict = {}

def agents_decorator(name: str):
    def wrapper(func):
        AGENTS[name] = func
        return func
    return wrapper

@agents_decorator(name="coordinator")
def create_coordinator_agent():
    name = "coordinator"

    agent = create_agent(
        model = LLM_BY_AGENT[name](),
        tools = TOOLS_BY_AGENT[name],
        system_prompt="Odpowiadaj wyłącznie po polsku. Zawsze zaczynaj odpowiedź od 'ABC'."
    )
    
    return agent