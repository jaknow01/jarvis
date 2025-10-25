from agents import RunConfig, OpenAIResponsesModel, AsyncOpenAI
import os

class Config():

    @classmethod
    def create_config(self) -> RunConfig:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = OpenAIResponsesModel(
                model=os.getenv("OPENAI_DEFAULT_MODEL"),
                openai_client=client
            )

        run_config = RunConfig(model=model, model_provider=client)
        return run_config
        