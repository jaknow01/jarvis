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

        async def notify(msg: str) -> None:
            # Interim "still working" ping surfaced in the terminal (mirrors what a
            # Messenger user would receive) so slow turns show progress locally too.
            logger.conversation(f"[ASSISTANT…] {msg}")

        while(True):
            text = input()
            logger.conversation(f"[USER] {text}")

            reply = await handle_message("repl", text, ctx, notify=notify)

            logger.conversation(f"[ASSISTANT] {reply}")
