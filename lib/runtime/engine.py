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

import asyncio
import logging
import time
from typing import Awaitable, Callable

from llama_index.core.agent.workflow import ToolCallResult
from llama_index.core.workflow import Context

from lib.agents import create_coordinator_agent, create_composer_agent
from lib.core import Ctx, bind_app_context, reset_app_context
from lib.domain import conversations
from lib.runtime import diag, progress
from lib.runtime.history_window import build_chat_history, schedule_summary_fold
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


async def _gather_with_progress_ping(handler, notify, want_ping):
    """Await the coordinator `handler`, optionally pushing one interim progress ping.

    When `want_ping`, two background tasks run alongside the await: one consumes the
    handler's event stream to record which subagents have already returned, the other
    sleeps `progress.ping_after_seconds()` and then fires a single `notify(...)` with a
    "still working" message listing them. If the handler finishes before that timeout,
    the pending ping is cancelled and never fires. Both helpers are best-effort and
    swallow their own errors so tracing/telemetry can never break a turn.

    On cancellation (the transport cancelling this turn via /stop), the coordinator's
    underlying workflow task is cancelled too — not just this awaiter — so the in-flight
    LLM call actually stops rather than finishing detached.

    Returns the handler's result (the coordinator's gathered summary).
    """
    collector = pinger = None
    if want_ping:
        done_labels: list[str] = []

        async def _collect_progress() -> None:
            async for ev in handler.stream_events():
                if isinstance(ev, ToolCallResult):
                    lbl = progress.label_for(ev.tool_name)
                    if lbl and lbl not in done_labels:
                        done_labels.append(lbl)

        async def _ping_after_timeout() -> None:
            await asyncio.sleep(progress.ping_after_seconds())
            try:
                await notify(progress.progress_message(done_labels))
            except Exception:
                logger.exception("Interim progress ping failed (non-fatal)")

        collector = asyncio.create_task(_collect_progress())
        pinger = asyncio.create_task(_ping_after_timeout())

    try:
        return await handler
    except asyncio.CancelledError:
        # /stop cancelled this turn: propagate into the workflow so its internal task
        # (and the in-flight LLM request) is cancelled, not left running detached.
        try:
            handler.cancel()
        except Exception:
            pass
        raise
    finally:
        # Turn finished (possibly before the timeout) -> cancel the pending ping so it
        # never fires, and stop the (usually already-finished) event collector.
        for task in (pinger, collector):
            if task is not None:
                task.cancel()


async def handle_message(
    conversation_id: str,
    text: str,
    ctx: Ctx,
    origin: str = "user",
    notify: Callable[[str], Awaitable[None]] | None = None,
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
        notify: optional async "push one message to the user now" callback, wired by
            the transport (Messenger Send API / REPL print). When set on a live user
            turn, a single interim "still working" ping fires if the coordinator runs
            longer than `PROGRESS_PING_AFTER_SECONDS`, listing the subagents already
            returned (see `lib/runtime/progress.py`). Passed per-call rather than on the
            shared `Ctx` because one `Ctx` is reused across concurrent Messenger senders.

    Returns:
        The composer's final natural-language reply (Polish).
    """
    # Let tools (e.g. the scheduler) know which channel/target this turn belongs to,
    # and make the shared Ctx reachable from every tool for the duration of the turn.
    ctx.conversation_id = conversation_id
    token = bind_app_context(ctx)
    started = time.monotonic()
    try:
        # Bounded, context-engineered history: last N pairs verbatim, prefixed by an
        # incrementally-maintained running summary of everything older (see
        # lib/runtime/history_window.py). Read-only and LLM-free — the summary fold runs
        # off the critical path (scheduled after the reply below). Falls back to full
        # history when disabled.
        history = await build_chat_history(conversation_id)
        input_text = (_SYSTEM_PROMPT_MARKER + text) if origin == "system" else text

        # 1) Coordinator gathers results from the subagent tools (never writes the reply).
        #    On a live user turn with a notify callback we watch its event stream so a
        #    slow turn pushes one interim "still working" ping (naming the subagents
        #    already done). Ordering is race-free: the transport sends the final reply
        #    only after this function returns, so any ping necessarily precedes it.
        coordinator = create_coordinator_agent()
        handler = coordinator.run(
            user_msg=input_text, chat_history=history, ctx=Context(coordinator)
        )
        want_ping = notify is not None and origin == "user" and progress.ping_enabled()
        gathered = str(await _gather_with_progress_ping(handler, notify, want_ping))

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
        trace_id = last_trace_id()
        conversations.record_turn(conversation_id, text, reply, trace_id=trace_id)
        diag.record_turn_stats(conversation_id, time.monotonic() - started, trace_id)

        # 4) Fold any freshly-evicted history into the running summary in the background,
        #    now that this turn is recorded — off the critical path, so the reply above was
        #    never blocked on the compressor LLM call. Coalesced per conversation.
        schedule_summary_fold(conversation_id)
        return reply
    finally:
        reset_app_context(token)
