from lib.core import Cache, Ctx
from lib.domain import seed_owner, OWNER_USER_ID
from lib.runtime.engine import handle_message
from lib.runtime import admin, diag
import logging

logger = logging.getLogger(__name__)

class Chatbot():
    async def start_chatbot(self):

        # print("Wiadomość powitalna")
        logger.conversation("[ASSISTANT] Wiadomość powitalna")
        ctx = Ctx()
        ctx.cache = Cache()
        # The local REPL always acts as the owner (a single local operator). Seed ensures
        # the owner user/default group exist so profile resolution and per-user scoping work.
        seed_owner()

        async def notify(msg: str) -> None:
            # Interim "still working" ping surfaced in the terminal (mirrors what a
            # Messenger user would receive) so slow turns show progress locally too.
            logger.conversation(f"[ASSISTANT…] {msg}")

        while(True):
            text = input()
            logger.conversation(f"[USER] {text}")

            # Technical commands (/status, /logs, …) are handled deterministically,
            # bypassing the agent stack — same surface the Messenger owner gets.
            diag.record_inbound("repl")
            admin_reply = await admin.maybe_handle(text, conversation_id="repl", channel="repl")
            if admin_reply is not None:
                logger.conversation(f"[ADMIN]\n{admin_reply}")
                continue

            reply = await handle_message("repl", text, ctx, notify=notify, user_id=OWNER_USER_ID)

            logger.conversation(f"[ASSISTANT] {reply}")
