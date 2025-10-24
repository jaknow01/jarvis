from lib.agents import create_coordinator_agent
from agents import Runner
from lib.cache import Cache
from lib.run_config import Config

class Chatbot():
    async def start_chatbot(self):
        print("Wiadomość powitalna")
        cache = Cache()

        while(True):
            text = input()
            run_config = Config.create_config()
            coordinator = create_coordinator_agent()

            prev_id = await cache.get_from_cache("previous_response_id")
            result = await Runner.run(
                coordinator,
                input = text,
                run_config=run_config,
                previous_response_id= prev_id
            )

            await cache.save_to_cache("previous_response_id", result.last_response_id)
            print(result.final_output)

# TODO
# 1. Zacząć zachowywać previous response id w redisie -> czyli napisać kod do zapisu i odczytu z redisa --DONE
# 2. Ogarnąć co z tym ctx dla narzędzi - potencjalnie kod dla tego kontekstu ale nie wiem co by się miało w nim znajdować
# 3. Przenieść tworzenie run_configu do oddzielnego pliku --DONE
# 4. Jakieś testowe narzędzia
# 5. ZAPOMNIEĆ O LANGCHAINIE - ALL MY HOMIES HATE LANGCHAIN