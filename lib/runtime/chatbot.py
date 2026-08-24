from lib.core import Cache, Ctx
from lib.runtime.engine import handle_message
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

            reply = await handle_message("repl", text, ctx)

            logger.conversation(f"[ASSISTANT] {reply}")
