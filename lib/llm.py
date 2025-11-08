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



@llm_usage(["coordinator", "iot_operator", "maps_agent", "memory_operator" , "finance_agent"])
def open_ai_llm() -> dict:
    settings = ModelSettings(parallel_tool_calls=True)

    return {
        "model_name" : os.getenv("OPENAI_DEFAULT_MODEL"),
        "settings" : settings
    }

@llm_usage(["news_agent"])
def xai_llm() -> dict:
    model = "xai/grok-4-fast-non-reasoning"
    api_key = os.getenv("XAI_API_KEY")

    settings = ModelSettings(include_usage=True, parallel_tool_calls=True, tool_choice='required')
    lite_model = LitellmModel(model=model, api_key=api_key)

    return {
        "model_name" : lite_model,
        "settings" : settings
    }