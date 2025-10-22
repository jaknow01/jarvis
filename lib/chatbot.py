from lib.agents import create_coordinator_agent
from agents import OpenAIResponsesModel, AsyncOpenAI, RunConfig, Runner
import os

class Chatbot():
    async def start_chatbot(self):
        print("Wiadomość powitalna")

        while(True):
            text = input()
            
            client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            model = OpenAIResponsesModel(
                model=os.getenv("OPENAI_DEFAULT_MODEL"),
                openai_client=client
            )

            run_config = RunConfig(model=model, model_provider=client)
            coordinator = create_coordinator_agent()

            result = await Runner.run(
                coordinator,
                input = text,
                run_config=run_config,
            )

            print(result.final_output)

# TODO
# 1. Zacząć zachowywać previous response id w redisie -> czyli napisać kod do zapisu i odczytu z redisa
# 2. Ogarnąć co z tym ctx dla narzędzi - potencjalnie kod dla tego kontekstu ale nie wiem co by się miało w nim znajdować
# 3. Przenieść tworzenie run_configu do oddzielnego pliku
# 4. Jakieś testowe narzędzia
# 5. ZAPOMNIEĆ O LANGCHAINIE - ALL MY HOMIES HATE LANGCHAIN