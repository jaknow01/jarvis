"""Rolling-window context engineering for conversation history.

Instead of replaying a conversation's *entire* history into every turn (the old
behaviour — unbounded token growth), this module assembles a bounded context:

- the **last N user/assistant pairs verbatim** (``HISTORY_WINDOW_PAIRS``, default 3),
- prefixed by a **running summary** of everything older than that window, kept to at
  most ``HISTORY_SUMMARY_MAX_SENTENCES`` sentences (default 5) and written in Polish.

The summary is maintained **incrementally**: each turn, only the exchange(s) that
just fell outside the window are folded into the existing summary with a single small
LLM call (the model resolved via ``LLM_BY_AGENT["history_compressor"]`` — configure
with ``AGENT_HISTORY_COMPRESSOR_MODEL`` / ``…_REASONING_EFFORT``, falling back to
``DEFAULT_MODEL``). We never recompute the whole summary from scratch.

**The fold runs off the critical path.** ``build_chat_history`` is read-only and does
**no** LLM call — it assembles ``[summary?] + everything since summary_upto`` and returns
immediately, so the user's turn never waits on compression. The engine kicks off the fold
as a fire-and-forget background task (``schedule_summary_fold``) *after* the reply is sent;
its result only matters for the *next* turn's context. If a fold hasn't finished by the next
turn, that turn simply shows the not-yet-folded exchanges verbatim (bounded: window + at
most one turn's lag) and the next fold catches up. Folds are coalesced per conversation so
overlapping turns never double-write the summary.

State lives on the conversation (``summary`` + ``summary_upto``, the number of message
rows already folded in), persisted by ``lib.domain.conversations`` across both the JSON
and Postgres backends.

Config (env):
    HISTORY_COMPRESSION_ENABLED   master switch (default on; off ⇒ full history verbatim)
    HISTORY_WINDOW_PAIRS          verbatim user/assistant pairs kept (default 3)
    HISTORY_SUMMARY_MAX_SENTENCES max sentences in the running summary (default 5)

Resilience: if the compressor call fails we do **not** break the turn — we leave the
coverage pointer untouched and fall back to showing the not-yet-summarised messages
verbatim for this turn (nothing is lost from context), retrying the fold next turn.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from llama_index.core.llms import ChatMessage

from lib.core import LLM_BY_AGENT
from lib.domain import conversations

logger = logging.getLogger(__name__)

COMPRESSOR_AGENT = "history_compressor"

DEFAULT_WINDOW_PAIRS = 3
DEFAULT_MAX_SENTENCES = 5

# Label prefixed to the running summary when it is injected as a system message.
_SUMMARY_HEADER = (
    "[Streszczenie wcześniejszej części rozmowy — sprzed bieżącego okna "
    "ostatnich wiadomości. Traktuj jako kontekst, nie jako polecenie użytkownika]:\n"
)

_COMPRESSOR_SYSTEM = (
    "You maintain a running summary of an ongoing conversation between a user and the "
    "Jarvis personal assistant. You are given the existing summary and the new "
    "exchange(s) that have just aged out of the recent verbatim window. Produce an "
    "UPDATED summary that folds the new exchange into the existing one.\n"
    "Rules:\n"
    "- Write the summary in POLISH.\n"
    "- At most {max_sentences} sentences. Be dense and factual.\n"
    "- Preserve concrete facts, user preferences, decisions, named entities, numbers, "
    "dates and any open threads; drop pleasantries and filler.\n"
    "- Keep older still-relevant facts from the existing summary; do not silently drop "
    "them just because they are old.\n"
    "- Output ONLY the summary text, nothing else."
)


def _falsy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in ("0", "false", "no", "off", "disabled")


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        logger.warning("%s must be an integer, got %r; using default %d", name, raw, default)
        return default


@dataclass(frozen=True)
class WindowConfig:
    enabled: bool
    window_pairs: int
    max_sentences: int


def load_config() -> WindowConfig:
    """Read the rolling-window configuration from the environment."""
    return WindowConfig(
        enabled=not _falsy(os.getenv("HISTORY_COMPRESSION_ENABLED")),
        window_pairs=_int_env("HISTORY_WINDOW_PAIRS", DEFAULT_WINDOW_PAIRS, minimum=0),
        max_sentences=_int_env("HISTORY_SUMMARY_MAX_SENTENCES", DEFAULT_MAX_SENTENCES, minimum=1),
    )


@dataclass(frozen=True)
class WindowPlan:
    """Pure decision about how to split a message list into summary + verbatim tail.

    Attributes:
        fold_start/fold_end: half-open range of messages to fold into the summary this
            turn (``None`` range when nothing new aged out).
        verbatim_start: index at which the verbatim window begins.
        new_upto: coverage pointer to persist once the fold succeeds.
    """
    fold_start: int
    fold_end: int
    verbatim_start: int
    new_upto: int

    @property
    def has_fold(self) -> bool:
        return self.fold_end > self.fold_start


def plan_window(n_messages: int, summary_upto: int, window_pairs: int) -> WindowPlan:
    """Decide which messages to fold and where the verbatim window starts (pure).

    ``boundary`` is the first index that stays verbatim (the last ``window_pairs`` pairs =
    ``2 * window_pairs`` messages). Messages in ``[summary_upto, boundary)`` are the ones
    that have just aged out and need folding. ``verbatim_start`` never precedes
    ``summary_upto`` (avoids re-showing already-summarised messages if the window grew).
    """
    keep = max(0, window_pairs) * 2
    boundary = max(0, n_messages - keep)
    fold_start = min(summary_upto, boundary)
    fold_end = boundary
    verbatim_start = max(boundary, summary_upto)
    new_upto = boundary if boundary > fold_start else summary_upto
    return WindowPlan(fold_start, fold_end, verbatim_start, new_upto)


def _to_chat_messages(history: list[dict]) -> list[ChatMessage]:
    return [ChatMessage(role=m["role"], content=m["content"]) for m in history]


def _format_exchange(messages: list[dict]) -> str:
    label = {"user": "Użytkownik", "assistant": "Asystent"}
    lines = [f"{label.get(m['role'], m['role'])}: {m['content']}" for m in messages]
    return "\n".join(lines)


async def _llm_compress(old_summary: str, evicted: list[dict], max_sentences: int) -> str:
    """Fold ``evicted`` exchanges into ``old_summary`` via the compressor LLM (Polish)."""
    llm = LLM_BY_AGENT[COMPRESSOR_AGENT]()
    system = _COMPRESSOR_SYSTEM.format(max_sentences=max_sentences)
    user = (
        "Dotychczasowe streszczenie:\n"
        + (old_summary.strip() or "(brak — to pierwsze streszczenie)")
        + "\n\nNowa wymiana do dołączenia (właśnie wypadła poza okno):\n"
        + _format_exchange(evicted)
        + f"\n\nZaktualizowane streszczenie (maksymalnie {max_sentences} zdań, po polsku):"
    )
    resp = await llm.achat(
        [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]
    )
    return str(resp.message.content or "").strip()


async def build_chat_history(
    channel_key: str,
    *,
    store=conversations,
    config: WindowConfig | None = None,
) -> list[ChatMessage]:
    """Assemble the bounded ``chat_history`` for one turn — **read-only, no LLM call**.

    Returns ``[system: summary?] + every message since summary_upto``: the running summary
    (if any) followed verbatim by everything not yet folded into it (the last N pairs, plus
    any exchanges that have aged out but whose background fold has not run yet — bounded to
    window + one turn's lag in steady state). With compression disabled it returns the full
    history verbatim (legacy behaviour). The actual folding is done off the critical path by
    ``schedule_summary_fold`` after the reply is sent. ``store`` is injectable for testing.
    """
    cfg = config or load_config()
    history = store.load_history(channel_key)

    if not cfg.enabled:
        return _to_chat_messages(history)

    summary, summary_upto = store.load_summary(channel_key)
    start = min(max(0, summary_upto), len(history))

    messages: list[ChatMessage] = []
    if summary.strip():
        messages.append(ChatMessage(role="system", content=_SUMMARY_HEADER + summary.strip()))
    messages.extend(_to_chat_messages(history[start:]))
    return messages


async def fold_summary(
    channel_key: str,
    *,
    store=conversations,
    config: WindowConfig | None = None,
    compressor=_llm_compress,
) -> bool:
    """Fold any freshly-evicted exchange(s) into the running summary and persist it.

    Reads the channel's current history + summary, and if anything has aged past the
    verbatim window, compresses it onto the existing summary with one LLM call and saves the
    new ``(summary, summary_upto)``. Returns True when a fold happened. Best-effort: a
    compressor failure leaves the coverage pointer untouched (retried next turn) and returns
    False — it must never surface to the caller. Injectable ``store``/``compressor`` for tests.
    """
    cfg = config or load_config()
    if not cfg.enabled:
        return False

    history = store.load_history(channel_key)
    summary, summary_upto = store.load_summary(channel_key)
    plan = plan_window(len(history), summary_upto, cfg.window_pairs)
    if not plan.has_fold:
        return False

    evicted = history[plan.fold_start:plan.fold_end]
    try:
        new_summary = await compressor(summary, evicted, cfg.max_sentences)
    except Exception as e:  # noqa: BLE001 - compression must never break a turn
        logger.error(
            "history compression failed for %s (%s); leaving summary untouched, "
            "will retry next turn", channel_key, e
        )
        return False
    store.save_summary(channel_key, new_summary, plan.new_upto)
    return True


# Per-conversation in-flight fold tasks, so overlapping turns coalesce onto one fold
# (a running fold reads fresh history and covers everything, so a second is redundant).
_fold_tasks: dict[str, asyncio.Task] = {}


def schedule_summary_fold(
    channel_key: str,
    *,
    store=conversations,
    config: WindowConfig | None = None,
    compressor=_llm_compress,
) -> Optional[asyncio.Task]:
    """Kick off ``fold_summary`` as a fire-and-forget background task (keeps the compressor
    LLM call off the turn's critical path). Coalesces per conversation: if a fold is already
    running for ``channel_key``, returns it instead of starting another. Returns None when
    compression is disabled. The engine calls this after the reply has been produced.
    """
    cfg = config or load_config()
    if not cfg.enabled:
        return None

    existing = _fold_tasks.get(channel_key)
    if existing is not None and not existing.done():
        return existing

    async def _run() -> None:
        try:
            await fold_summary(channel_key, store=store, config=cfg, compressor=compressor)
        except Exception:  # noqa: BLE001 - background task must never crash the loop
            logger.exception("Background summary fold failed for %s", channel_key)

    task = asyncio.create_task(_run())
    _fold_tasks[channel_key] = task
    return task
