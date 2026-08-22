from agents import RunConfig
from agents.models.openai_provider import OpenAIProvider
import os

class Config():

    @classmethod
    def create_config(cls) -> RunConfig:
        # Resolve each agent's model per-agent rather than pinning one model for
        # the whole run. If RunConfig.model is set (as a string or a Model
        # instance) the SDK uses it for EVERY agent and ignores each agent's own
        # `model=` (see Runner._get_model). We want the coordinator to run on its
        # own "smarter" reasoning model while the subagents keep their configured
        # models, so we leave RunConfig.model unset and let the provider resolve
        # the string model names each agent carries (from lib/llm.py).
        #
        # use_responses=True keeps every agent on the OpenAI Responses API, which
        # conversation continuity depends on (previous_response_id in lib/engine.py).
        provider = OpenAIProvider(
            api_key=os.getenv("OPENAI_API_KEY"),
            use_responses=True,
        )
        return RunConfig(model_provider=provider)
