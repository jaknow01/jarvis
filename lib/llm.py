from agents import ModelSettings
from agents.extensions.models.litellm_model import LitellmModel
import os

LLM_BY_AGENT: dict = {}

def llm_usage(agent_names: list[str]):
    def wrapper(func):
        for agent in agent_names:
            LLM_BY_AGENT[agent] = func
        return func
    return wrapper



@llm_usage(["coordinator", "iot_operator", "maps_agent", "memory_operator" , "finance_agent" , "weather_agent"])
def open_ai_llm() -> dict:
    settings = ModelSettings(parallel_tool_calls=True)
    return {
        "model_name" : os.getenv("OPENAI_DEFAULT_MODEL"),
        "settings" : settings
    }

@llm_usage(["news_agent"])
def open_ai_reasoning_llm() -> dict:
    model_name = "gpt-5-mini"
    reasoning = {"effort" : "medium"}

    settings = ModelSettings(
        parallel_tool_calls=True,
        reasoning=reasoning
    )

    return {
        "model_name" : model_name,
        "settings" : settings
    }