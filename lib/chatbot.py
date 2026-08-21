from lib.cache import Cache, Ctx
from lib.engine import handle_message
from lib.hooks import LoggingRunHooks
import logging

logger = logging.getLogger(__name__)

class Chatbot():
    async def start_chatbot(self):

        # print("Wiadomość powitalna")
        logger.conversation("[ASSISTANT] Wiadomość powitalna")
        ctx = Ctx()
        ctx.cache = Cache()
        hooks = LoggingRunHooks()

        while(True):
            text = input()
            logger.conversation(f"[USER] {text}")

            reply = await handle_message("repl", text, ctx, hooks)

            logger.conversation(f"[ASSISTANT] {reply}")
