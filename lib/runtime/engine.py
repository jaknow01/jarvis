"""Transport-agnostic core: handle one message, return one reply.

This is the seam described in CLAUDE.md — the single entry point both the REPL
(`lib/runtime/chatbot.py`) and the Messenger webhook (`app/webhook.py`) call. Given
a conversation id and the user's text, it:

1. binds the shared per-run `Ctx` for the turn (contextvar; reachable from every tool),
2. loads the full prior history for the conversation and replays it as `chat_history`,
3. runs the coordinator (LlamaIndex `FunctionAgent`), which gathers results from the
   sub-agents and writes the answer's content,
4. runs the composer deterministically to format that answer as plain Polish chat text
   (form only — it never changes the content),
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

from lib.agents import create_coordinator_agent, create_composer_agent, is_sensitive, subagent_names
from lib.core import (
    Ctx,
    bind_app_context,
    reset_app_context,
    bind_model_overrides,
    reset_model_overrides,
    group_scope_for,
    env_int,
    refresh_mcp_toolsets,
    bind_mcp_principal,
    reset_mcp_principal,
)
from lib.domain import (
    conversations,
    principals,
    resolve_profile,
    agent_config,
    GROUP_SCOPE,
    DEFAULT_GROUP_ID,
)
from lib.runtime import diag, progress
from lib.runtime.reply import Reply
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


# Stored in the conversation history instead of a reply built from a sensitive sub-agent
# (e.g. cluster state): the owner got the full text, but it is not kept in the database, in
# the rolling summary or in later prompts.
SENSITIVE_HISTORY_PLACEHOLDER = (
    "[Odpowiedź zawierała dane poufne (np. stan klastra) — treść nie jest przechowywana w historii.]"
)


def coordinator_timeout_seconds() -> int:
    """Wall-clock cap on the coordinator's gather phase, ``COORDINATOR_TIMEOUT_SECONDS``
    (default 300; ``0`` disables). Past it the run is stopped and the composer writes the
    reply from whatever sub-agent results already came back."""
    return env_int("COORDINATOR_TIMEOUT_SECONDS", 300, minimum=0)


def _partial_gather(results: list[tuple[str, str]], timeout: int) -> str:
    """The answer used when the coordinator hit its deadline (it wrote no answer): a fixed
    notice plus the sub-agent results that did arrive. The content comes from code and
    the sub-agents, so the composer only has to format it."""
    logger.warning("Coordinator hit its %ss deadline; answering from partial results", timeout)
    if not results:
        return (
            "Nie udało mi się zebrać potrzebnych informacji w wyznaczonym czasie. "
            "Spróbuj zapytać ponownie za chwilę."
        )
    body = "\n\n".join(output for _name, output in results)
    return (
        "Nie zdążyłem zebrać wszystkich informacji w wyznaczonym czasie, więc część odpowiedzi "
        "może być niepełna. Oto, co udało się ustalić:\n\n" + body
    )


# Fixed answers for a turn where the coordinator only declared the message conversational
# (`no_data_needed`) and ran no sub-agent. The content comes from code — not from an LLM —
# so this path has nothing it could invent; the composer just formats it.
_NO_DATA_ANSWERS = {
    "greeting": "Cześć! W czym mogę pomóc?",
    "thanks": "Nie ma za co!",
    "small_talk": "Jestem do dyspozycji. W czym mogę pomóc?",
    "acknowledgement": "Jasne.",
    "farewell": "Do usłyszenia!",
}
_NO_DATA_DEFAULT = "Jestem do dyspozycji. W czym mogę pomóc?"


def _guard_no_data_turn(turn_ctx: Ctx, answer: str) -> str:
    """Replace the coordinator's answer on a turn where it only called the `no_data_needed`
    escape tool (no sub-agent ran): nothing was gathered, so any facts in that text would be
    the model's own invention. A fixed answer for the declared kind is used instead, so
    misuse of the escape tool yields a generic reply, never fabricated data."""
    if turn_ctx.no_data_kind and not turn_ctx.subagent_calls:
        logger.info("No-data turn (kind=%s): coordinator answer replaced", turn_ctx.no_data_kind)
        return _NO_DATA_ANSWERS.get(turn_ctx.no_data_kind, _NO_DATA_DEFAULT)
    return answer


async def _gather_with_progress_ping(handler, notify, want_ping, timeout: int = 0):
    """Await the coordinator `handler`, optionally pushing one interim progress ping.

    An event collector always consumes the handler's stream, recording each finished
    subagent (for the ping's labels) and its output (for the timeout fallback). When
    `want_ping`, a second task sleeps `progress.ping_after_seconds()` and then fires a
    single `notify(...)` with a "still working" message listing the finished subagents. If
    the handler finishes before that, the pending ping is cancelled and never fires. Both
    helpers are best-effort and swallow their own errors so telemetry can never break a turn.

    When `timeout` (seconds) is set and the coordinator outlasts it, the workflow is
    cancelled and the partial results gathered so far are returned instead (see
    `_partial_gather`), so a hung LLM call or a tool loop can't hold the reply forever.

    On cancellation (the transport cancelling this turn via /stop), the coordinator's
    underlying workflow task is cancelled too — not just this awaiter — so the in-flight
    LLM call actually stops rather than finishing detached.

    Returns the handler's result (the coordinator's answer).
    """
    done_labels: list[str] = []
    results: list[tuple[str, str]] = []

    async def _collect_progress() -> None:
        try:
            async for ev in handler.stream_events():
                if isinstance(ev, ToolCallResult):
                    lbl = progress.label_for(ev.tool_name)
                    if lbl and lbl not in done_labels:
                        done_labels.append(lbl)
                    results.append((ev.tool_name, str(ev.tool_output)))
        except Exception:
            logger.debug("Coordinator event collector stopped", exc_info=True)

    async def _ping_after_timeout() -> None:
        await asyncio.sleep(progress.ping_after_seconds())
        try:
            await notify(progress.progress_message(done_labels))
        except Exception:
            logger.exception("Interim progress ping failed (non-fatal)")

    collector = asyncio.create_task(_collect_progress())
    pinger = asyncio.create_task(_ping_after_timeout()) if want_ping else None

    try:
        return await asyncio.wait_for(_await_handler(handler), timeout=timeout or None)
    except asyncio.TimeoutError:
        logger.warning(
            "Coordinator exceeded %ss; composing from %d partial result(s)", timeout, len(results)
        )
        try:
            await handler.cancel_run()
        except Exception:
            logger.debug("Coordinator cancel after timeout failed (ignored)", exc_info=True)
        return _partial_gather(results, timeout)
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


async def _await_handler(handler):
    return await handler


def ensure_default_group_roster() -> None:
    """Seed the ``default`` group's roster with every subagent enabled, so the migrated
    single-user owner (member of ``default``) gets a full roster without per-agent config —
    preserving the pre-multi-user behavior under the new opt-in model. Seeds **once**: if the
    default group already has any agent_config rows (from a prior seed, admin edit, or the
    declarative config file), it is left untouched. Called at startup, before the config
    file is applied. Lives here (runtime) because it needs the subagent catalog (agents)."""
    if agent_config.get_layer(GROUP_SCOPE, DEFAULT_GROUP_ID):
        return
    for name in subagent_names():
        agent_config.set(GROUP_SCOPE, DEFAULT_GROUP_ID, name, enabled=True)
    logger.info("Seeded default group roster (all subagents enabled) for the owner")


def _resource_group_id(user_id: str | None) -> str | None:
    """The group whose shared physical resources (devices/maps) back this turn's Ctx.

    A user may belong to several groups (a config-layering concern); household resources
    belong to one group, so we use the user's **highest-priority** group. ``groups_for_user``
    returns groups ascending by priority, so the last one is highest. None when the user has
    no group (falls back to an uncached, empty group scope)."""
    if not user_id:
        return None
    groups = principals.groups_for_user(user_id)
    return groups[-1]["id"] if groups else None


async def handle_message(
    conversation_id: str,
    text: str,
    ctx: Ctx,
    origin: str = "user",
    notify: Callable[[str], Awaitable[None]] | None = None,
    user_id: str | None = None,
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
        The composer's final natural-language reply (Polish), as a :class:`Reply` (a ``str``)
        whose ``sensitive`` flag tells the transport not to log its content.
    """
    # Resolve the acting principal's profile (roster + per-agent model/reasoning) and
    # compose a per-turn Ctx scoped to that principal: a cached group scope for shared
    # physical resources + fresh per-user state. The group scope is keyed by the user's
    # highest-priority group so a household reuses one device inventory.
    group_id = _resource_group_id(user_id)
    profile = resolve_profile(user_id, subagent_names())
    overrides = {
        name: {"model": profile.model(name), "reasoning": profile.reasoning(name)}
        for name in profile.agents
    }
    turn_ctx = Ctx(
        cache=ctx.cache,
        group_scope=group_scope_for(group_id),
        user_id=user_id,
        group_id=group_id,
        conversation_id=conversation_id,
        profile=profile,
    )
    token = bind_app_context(turn_ctx)
    override_token = bind_model_overrides(overrides)
    # Tool servers holding personal data get the person / household this turn acts for
    # (X-On-Behalf-Of, per each sub-agent's `identity` in config/subagents.yaml).
    principal_token = bind_mcp_principal(user_id, group_id)
    started = time.monotonic()
    try:
        # Bounded, context-engineered history: last N pairs verbatim, prefixed by an
        # incrementally-maintained running summary of everything older (see
        # lib/runtime/history_window.py). Read-only and LLM-free — the summary fold runs
        # off the critical path (scheduled after the reply below). Falls back to full
        # history when disabled.
        # Alongside it: refresh the tool servers' listings — agent factories are sync, so
        # the listing must be cached before the coordinator is built. A no-op while fresh;
        # never raises (see lib/core/mcp_client.py).
        history, _ = await asyncio.gather(build_chat_history(conversation_id), refresh_mcp_toolsets())
        input_text = (_SYSTEM_PROMPT_MARKER + text) if origin == "system" else text

        # 1) Coordinator gathers results from the subagent tools and writes the answer.
        #    On a live user turn with a notify callback we watch its event stream so a
        #    slow turn pushes one interim "still working" ping (naming the subagents
        #    already done). Ordering is race-free: the transport sends the final reply
        #    only after this function returns, so any ping necessarily precedes it.
        # Sensitive sub-agents (cluster) only on a live turn of the person themself, never in a
        # scheduled job that would run unattended.
        coordinator = create_coordinator_agent(profile, allow_sensitive=(origin == "user"))
        handler = coordinator.run(
            user_msg=input_text, chat_history=history, ctx=Context(coordinator)
        )
        want_ping = notify is not None and origin == "user" and progress.ping_enabled()
        answer = str(await _gather_with_progress_ping(
            handler, notify, want_ping, timeout=coordinator_timeout_seconds()
        ))
        answer = _guard_no_data_turn(turn_ctx, answer)

        # 2) Composer formats the coordinator's answer, deterministically (always runs). It
        #    gets ONLY that answer — no user message, no history — so it can't add content.
        composer = create_composer_agent()
        composer_input = (
            "Sformatuj poniższą odpowiedź jako zwykły tekst wiadomości po polsku, bez zmiany "
            "jej treści:\n\n" + answer
        )
        reply = str(await composer.run(user_msg=composer_input, ctx=Context(composer)))

        # 3) Persist the turn (user msg + assistant reply), stamping the MLflow trace id. A reply
        #    built from a sensitive sub-agent is stored as a placeholder, never as its content.
        sensitive = any(is_sensitive(name) for name in turn_ctx.subagent_calls)
        stored = SENSITIVE_HISTORY_PLACEHOLDER if sensitive else reply
        trace_id = last_trace_id()
        conversations.record_turn(conversation_id, text, stored, trace_id=trace_id)
        diag.record_turn_stats(conversation_id, time.monotonic() - started, trace_id)

        # 4) Fold any freshly-evicted history into the running summary in the background,
        #    now that this turn is recorded — off the critical path, so the reply above was
        #    never blocked on the compressor LLM call. Coalesced per conversation.
        schedule_summary_fold(conversation_id)
        return Reply(reply, sensitive=sensitive)
    finally:
        reset_mcp_principal(principal_token)
        reset_app_context(token)
        reset_model_overrides(override_token)
