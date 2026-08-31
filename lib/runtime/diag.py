"""Process-wide diagnostic state for the admin command surface.

A tiny, dependency-free module (stdlib only) that other runtime modules write to and the
admin commands (`lib/runtime/admin.py`) read from. Kept separate precisely so it imports
nothing from `lib` — `engine` and `scheduler_runner` can record into it without creating
an import cycle back through `admin`.

Everything here is best-effort, in-memory, and reset on process restart:

- **process start** — for uptime (`/status`, `/version`).
- **last turn stats** — wall-clock duration + MLflow trace id of the most recent turn
  (`/last`), recorded by `engine.handle_message`.
- **inbound tracker** — timestamp of the previous inbound message per conversation, used
  by `/window` to estimate the Messenger 24h RESPONSE window.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

# -- process start / uptime --------------------------------------------------------

_START_MONO = time.monotonic()
_START_WALL = datetime.now(timezone.utc)


def uptime_seconds() -> float:
    """Seconds since this process (module import) started."""
    return max(0.0, time.monotonic() - _START_MONO)


def started_at() -> datetime:
    """UTC wall-clock time the process started."""
    return _START_WALL


# -- last turn stats ---------------------------------------------------------------

_last_turn: dict | None = None


def record_turn_stats(conversation_id: str, duration_s: float, trace_id: str) -> None:
    """Record diagnostics for the just-completed turn (called by the engine)."""
    global _last_turn
    _last_turn = {
        "conversation_id": conversation_id,
        "duration_s": round(float(duration_s), 2),
        "trace_id": trace_id or "",
        "at": datetime.now(timezone.utc),
    }


def last_turn_stats() -> dict | None:
    """The most recent turn's stats, or None if no turn has completed yet."""
    return dict(_last_turn) if _last_turn else None


# -- inbound tracker (for the /window estimate) ------------------------------------

# Maps conversation_id -> the timestamp of its previous inbound message, i.e. the one
# BEFORE the message currently being handled. record_inbound() returns that previous
# value and then stores "now", so /window can report time since the last real message
# rather than the command it is being asked through.
_prev_inbound: dict[str, datetime] = {}
_last_inbound: dict[str, datetime] = {}


def record_inbound(conversation_id: str) -> datetime | None:
    """Note an inbound message for ``conversation_id``; return the PREVIOUS one's time."""
    now = datetime.now(timezone.utc)
    prev = _last_inbound.get(conversation_id)
    _prev_inbound[conversation_id] = prev
    _last_inbound[conversation_id] = now
    return prev


def previous_inbound_at(conversation_id: str) -> datetime | None:
    """Timestamp of the inbound message before the current one (None if first seen)."""
    return _prev_inbound.get(conversation_id)


# -- in-flight turns (for the /stop command) ---------------------------------------

# conversation_id -> the set of asyncio Tasks currently running a live turn for it.
# The transport (webhook _process) registers the task handling a user message so a
# concurrently-arriving /stop — which runs in its OWN task — can cancel it mid-turn.
_running: dict[str, set[asyncio.Task]] = {}


def register_running(conversation_id: str, task: asyncio.Task) -> None:
    """Mark ``task`` as the live turn for ``conversation_id`` (so /stop can cancel it)."""
    _running.setdefault(conversation_id, set()).add(task)


def clear_running(conversation_id: str, task: asyncio.Task) -> None:
    """Deregister a finished turn's task; drop the bucket once empty."""
    bucket = _running.get(conversation_id)
    if bucket is not None:
        bucket.discard(task)
        if not bucket:
            _running.pop(conversation_id, None)


def cancel_running(conversation_id: str) -> int:
    """Cancel every live turn for ``conversation_id``. Returns how many were cancelled."""
    bucket = _running.get(conversation_id, set())
    cancelled = 0
    for task in list(bucket):
        if not task.done():
            task.cancel()
            cancelled += 1
    return cancelled
