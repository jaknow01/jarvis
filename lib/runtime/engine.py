"""Transport-agnostic core: handle one message, return one reply.

This is the seam described in CLAUDE.md — the single entry point both the REPL
(`lib/runtime/chatbot.py`) and the Messenger webhook (`app/webhook.py`) call. Given
a conversation id and the user's text, it:

1. binds the shared per-run `Ctx` for the turn (contextvar; reachable from every tool),
2. loads the full prior history for the conversation and replays it as `chat_history`,
3. runs the coordinator (LlamaIndex `FunctionAgent`) to gather results,
4. runs the composer deterministically to write the final Polish reply,
5. persists the user message + the assistant reply (stamped with the MLflow trace id).

Conversation continuity is owned by us now (Postgres/JSON via
`lib.domain.conversations`), keyed **per channel** by `conversation_id` — the REPL,
Messenger, and any future channel never cross-contaminate context. The OpenAI
Responses API `previous_response_id` mechanism is gone.
"""

import logging

from llama_index.core.workflow import Context

from lib.agents import create_coordinator_agent, create_composer_agent
from lib.core import Ctx, bind_app_context, reset_app_context
from lib.domain import conversations
from lib.runtime.history_window import build_chat_history
from lib.runtime.tracing import last_trace_id

logger = logging.getLogger(__name__)


# Prefix wrapping a system-originated (scheduled) turn, so the coordinator knows this
# is an autonomous job to execute and turn into a message for the user — not a question
# typed by the user right now. The composer still writes the final reply in Polish.
_SYSTEM_PROMPT_MARKER = (
    "[ZAPLANOWANE ZADANIE — to jest automatyczne wywołanie z harmonogramu, nie "
    "bieżąca wiadomość użytkownika. Wykonaj poniższe zadanie i przygotuj gotową "
    "wiadomość do użytkownika:]\n"
)


async def handle_message(
    conversation_id: str,
    text: str,
    ctx: Ctx,
    origin: str = "user",
) -> str:
    """Run one turn for `conversation_id` and return the composer's Polish reply.

    Args:
        conversation_id: stable id scoping conversation continuity (e.g. "repl"
            for the terminal, or "messenger:{psid}" for a Messenger sender).
        text: the user's message, or — when `origin="system"` — the scheduled task.
        ctx: shared run context (carries the Redis cache and per-run state); bound
            as the turn's shared context so every tool can reach it.
        origin: "user" for a normal turn, or "system" for a scheduler-triggered
            proactive turn (the text is wrapped so the coordinator treats it as a job
            to execute and deliver, and continuity stays in the same conversation).

    Returns:
        The composer's final natural-language reply (Polish).
    """
    # Let tools (e.g. the scheduler) know which channel/target this turn belongs to,
    # and make the shared Ctx reachable from every tool for the duration of the turn.
    ctx.conversation_id = conversation_id
    token = bind_app_context(ctx)
    try:
        # Bounded, context-engineered history: last N pairs verbatim, prefixed by an
        # incrementally-maintained running summary of everything older (see
        # lib/runtime/history_window.py). Falls back to full history when disabled.
        history = await build_chat_history(conversation_id)
        input_text = (_SYSTEM_PROMPT_MARKER + text) if origin == "system" else text

        # 1) Coordinator gathers results from the subagent tools (never writes the reply).
        coordinator = create_coordinator_agent()
        gathered = str(
            await coordinator.run(
                user_msg=input_text, chat_history=history, ctx=Context(coordinator)
            )
        )

        # 2) Composer writes the final user-facing reply, deterministically (always runs).
        composer = create_composer_agent()
        composer_input = (
            "Wiadomość użytkownika:\n" + text + "\n\n"
            "Zebrane dane od podagentów (użyj TYLKO ich, nie wymyślaj):\n" + gathered + "\n\n"
            "Napisz teraz ostateczną odpowiedź dla użytkownika po polsku."
        )
        reply = str(
            await composer.run(
                user_msg=composer_input, chat_history=history, ctx=Context(composer)
            )
        )

        # 3) Persist the turn (user msg + assistant reply), stamping the MLflow trace id.
        conversations.record_turn(conversation_id, text, reply, trace_id=last_trace_id())
        return reply
    finally:
        reset_app_context(token)
