from lib.agents import create_coordinator_agent
from agents import Runner
from lib.cache import Cache, Ctx
from lib.run_config import Config
import logging

logger = logging.getLogger(__name__)

class Chatbot():
    async def start_chatbot(self):

        # print("Wiadomość powitalna")
        logger.conversation("[ASSISTANT] Wiadomość powitalna")
        ctx = Ctx()
        ctx.cache = Cache()

        while(True):
            text = input()
            logger.conversation(f"[USER] {text}")
            run_config = Config.create_config()
            coordinator = create_coordinator_agent()

            prev_id = await ctx.cache.get_from_cache("previous_response_id")
            result = await Runner.run(
                coordinator,
                input = text,
                run_config=run_config,
                previous_response_id= prev_id,
                context=ctx
            )

            await ctx.cache.save_to_cache("previous_response_id", result.last_response_id)
            logger.conversation(f"[ASSISTANT] {result.final_output}")