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



@llm_usage(["composer", "iot_operator", "maps_agent", "memory_operator" , "finance_agent" , "weather_agent", "fpl_agent"])
def open_ai_llm() -> dict:
    settings = ModelSettings(parallel_tool_calls=True)
    return {
        "model_name" : os.getenv("OPENAI_DEFAULT_MODEL"),
        "settings" : settings
    }

@llm_usage(["coordinator"])
def open_ai_coordinator_llm() -> dict:
    # The coordinator does the heaviest thinking of the network: it routes each
    # request to the right subagent(s), decides what to run in parallel, and
    # reconciles multi-domain results before handing off to the composer. It runs
    # on a dedicated, "smarter" reasoning model with explicit reasoning effort so
    # routing quality wins over latency (a deliberate trade-off).
    #
    # The env is the source of truth: COORDINATOR_MODEL picks the smart model
    # (falling back to OPENAI_DEFAULT_MODEL when unset) and
    # COORDINATOR_REASONING_EFFORT sets the reasoning effort (minimal/low/medium/
    # high; defaults to medium). Note: for a distinct coordinator model to take
    # effect, RunConfig must not pin a single model for the whole run — see
    # lib/run_config.py. The chosen model must be reasoning-capable.
    model_name = os.getenv("COORDINATOR_MODEL") or os.getenv("OPENAI_DEFAULT_MODEL")
    effort = (os.getenv("COORDINATOR_REASONING_EFFORT") or "medium").strip().lower()

    settings = ModelSettings(
        parallel_tool_calls=True,
        reasoning={"effort": effort},
    )
    return {
        "model_name" : model_name,
        "settings" : settings
    }

@llm_usage(["news_agent"])
def open_ai_reasoning_llm() -> dict:
    model_name = "gpt-5-mini"
    reasoning = {"effort" : "low"}

    settings = ModelSettings(
        parallel_tool_calls=True,
        reasoning=reasoning
    )

    return {
        "model_name" : model_name,
        "settings" : settings
    }