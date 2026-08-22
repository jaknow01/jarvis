"""Scheduler runner: the out-of-band loop that fires due jobs.

This is the **alternate entry path** into Jarvis. Instead of a user typing a message,
the loop wakes on a tick, finds jobs whose ``next_run_at`` has passed, and feeds each
job's prompt back into the very same ``lib.engine.handle_message`` a user turn uses —
but with ``origin="system"`` — then pushes the reply to the job's channel.

It runs in-process inside the long-running webhook service (started from
``app/webhook.py`` ``lifespan``). Everything here is best-effort: a failing job, a
delivery error, or a bad tick is logged and swallowed so the loop never dies and the
service is never taken down by scheduling.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Awaitable, Callable, Optional

from lib.cache import Cache, Ctx
from lib.engine import handle_message
from lib.messenger import send_message
from lib.scheduler import (
    describe_schedule,
    is_expired,
    next_after,
    now_tz,
)
from lib.scheduler import store as default_store

logger = logging.getLogger(__name__)

# Tag giving a 7-day window for proactive pushes outside the 24h Messenger window.
_PROACTIVE_TAG = "HUMAN_AGENT"


def scheduler_enabled() -> bool:
    """Master switch (env SCHEDULER_ENABLED); enabled unless set to a falsy value."""
    raw = os.getenv("SCHEDULER_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def tick_seconds() -> int:
    try:
        return max(1, int(os.getenv("SCHEDULER_TICK_SECONDS") or 30))
    except ValueError:
        return 30


# -- invocation: run a job's prompt as a system-originated turn ----------------

def _make_default_invoke(cache: Cache) -> Callable[[dict], Awaitable[str]]:
    """Build the default invoker. A fresh Ctx per run (sharing the Redis cache) keeps
    the loop from clobbering the webhook's shared ctx.conversation_id."""

    async def _invoke(job: dict) -> str:
        ctx = Ctx(cache=cache)
        return await handle_message(
            job["conversation_id"], job["prompt"], ctx, origin="system"
        )

    return _invoke


# -- delivery: push the reply to the job's channel -----------------------------

async def default_deliver(job: dict, reply: str) -> None:
    """Route a scheduled reply to its channel: Messenger (tagged) or the log."""
    channel = job.get("channel")
    if channel == "messenger" and job.get("target"):
        token = os.getenv("MESSENGER_PAGE_ACCESS_TOKEN", "")
        if not token:
            logger.error(
                "Scheduled job %s targets Messenger but MESSENGER_PAGE_ACCESS_TOKEN "
                "is unset; logging instead: %s", job["id"], reply,
            )
            return
        await send_message(
            token, job["target"], reply,
            messaging_type="MESSAGE_TAG", tag=_PROACTIVE_TAG,
        )
        logger.conversation(f"[SCHEDULED -> {job['conversation_id']}] {reply}")
    else:
        # REPL / log channel: no live socket to push to, so surface it in the log.
        logger.conversation(f"[SCHEDULED {job['conversation_id']}] {reply}")


# -- the tick and the loop -----------------------------------------------------

async def _run_job(store, invoke, deliver, job: dict, now: datetime) -> None:
    logger.info("Firing scheduled job %s (%s)", job["id"], describe_schedule(job))
    reply = None
    try:
        reply = await invoke(job)
    except Exception:
        logger.exception("Scheduled job %s: invocation failed", job["id"])
    if reply:
        try:
            await deliver(job, reply)
        except Exception:
            logger.exception("Scheduled job %s: delivery failed", job["id"])

    # Advance the schedule even if the run failed, to avoid a tight retry loop.
    job["run_count"] = job.get("run_count", 0) + 1
    job["last_run_at"] = now.isoformat()
    nxt = next_after(job, now)
    if is_expired(job, nxt):
        job["status"] = "done"
        job["next_run_at"] = None
        logger.info("Scheduled job %s completed (no further runs)", job["id"])
    else:
        job["next_run_at"] = nxt.isoformat()
        logger.info("Scheduled job %s re-armed for %s", job["id"], job["next_run_at"])
    store.save(job)


async def tick_once(store, invoke, deliver, now: Optional[datetime] = None) -> int:
    """Fire every job due at ``now`` (sequentially, to keep conversation continuity).

    Returns the number of jobs fired. Injectable ``invoke``/``deliver`` make this
    unit-testable without network or the model."""
    now = now or now_tz()
    due = store.due(now)
    for job in due:
        await _run_job(store, invoke, deliver, job, now)
    return len(due)


async def run_scheduler_loop(
    store=None,
    invoke: Optional[Callable[[dict], Awaitable[str]]] = None,
    deliver: Optional[Callable[[dict, str], Awaitable[None]]] = None,
    seconds: Optional[int] = None,
    cache: Optional[Cache] = None,
) -> None:
    """Run the scheduler forever: every `seconds`, fire due jobs. Never raises."""
    store = store or default_store
    cache = cache or Cache()
    invoke = invoke or _make_default_invoke(cache)
    deliver = deliver or default_deliver
    seconds = seconds or tick_seconds()
    logger.info("Scheduler loop started (tick=%ss)", seconds)
    while True:
        try:
            await tick_once(store, invoke, deliver)
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled; stopping")
            raise
        except Exception:
            logger.exception("Scheduler tick failed")
        await asyncio.sleep(seconds)
