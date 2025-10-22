from agents import ModelSettings
import os

LLM_BY_AGENT: dict = {}

def llm_usage(agent_names: list[str]):
    def wrapper(func):
        for agent in agent_names:
            LLM_BY_AGENT[agent] = func
        return func
    return wrapper



@llm_usage(["coordinator"])
def open_ai_llm() -> dict:
    settings = ModelSettings(parallel_tool_calls=True)

    return {
        "model_name" : os.getenv("OPENAI_DEFAULT_MODEL"),
        "settings" : settings
    }