"""Transport-agnostic core: handle one message, return one reply.

This is the seam described in CLAUDE.md — the single entry point both the REPL
(`lib/chatbot.py`) and the Messenger webhook (`app/webhook.py`) call. Given a
conversation id and the user's text, it builds a fresh coordinator + run config,
runs the OpenAI Agents SDK, and persists the `previous_response_id` so the
conversation stays continuous across turns.

Conversation continuity is keyed **per user**: each channel/user gets its own
`previous_response_id` under `previous_response_id:{conversation_id}` in Redis,
so the REPL, Messenger, and any future channel never cross-contaminate context.
"""

import logging

from agents import Runner

from lib.agents import create_coordinator_agent
from lib.cache import Ctx
from lib.hooks import LoggingRunHooks
from lib.run_config import Config

logger = logging.getLogger(__name__)


def _response_id_key(conversation_id: str) -> str:
    return f"previous_response_id:{conversation_id}"


async def handle_message(
    conversation_id: str,
    text: str,
    ctx: Ctx,
    hooks: LoggingRunHooks | None = None,
) -> str:
    """Run one turn of the coordinator for `conversation_id` and return the reply.

    Args:
        conversation_id: stable id scoping conversation continuity (e.g. "repl"
            for the terminal, or "messenger:{psid}" for a Messenger sender).
        text: the user's message.
        ctx: shared run context (carries the Redis cache and per-run state).
        hooks: optional run hooks; a fresh `LoggingRunHooks` is used if omitted.

    Returns:
        The coordinator's final natural-language reply.
    """
    hooks = hooks or LoggingRunHooks()
    run_config = Config.create_config()
    coordinator = create_coordinator_agent()

    key = _response_id_key(conversation_id)
    prev_id = await ctx.cache.get_from_cache(key)

    result = await Runner.run(
        coordinator,
        input=text,
        run_config=run_config,
        previous_response_id=prev_id,
        context=ctx,
        hooks=hooks,
    )

    await ctx.cache.save_to_cache(key, result.last_response_id)
    return result.final_output
