"""runtime layer — the top of the import DAG: entry points and channel adapters that
wire the agents network to a transport (REPL, Messenger webhook) and run scheduled jobs.

Imports go up only: runtime -> agents -> {domain, core}. This `__init__` is the public
surface consumed by `app/`.
"""

from lib.runtime.engine import handle_message
from lib.runtime.chatbot import Chatbot
from lib.runtime.tracing import setup_tracing, last_trace_id
from lib.runtime.scheduler_runner import run_scheduler_loop, scheduler_enabled
from lib.runtime.fpl_cache_runner import run_fpl_cache_loop, fpl_cache_refresh_enabled
from lib.runtime import admin, diag
from lib.runtime.messenger import (
    iter_message_events,
    send_message,
    verify_challenge,
    verify_signature,
)

__all__ = [
    "handle_message",
    "Chatbot",
    "setup_tracing",
    "last_trace_id",
    "run_scheduler_loop",
    "scheduler_enabled",
    "run_fpl_cache_loop",
    "fpl_cache_refresh_enabled",
    "admin",
    "diag",
    "iter_message_events",
    "send_message",
    "verify_challenge",
    "verify_signature",
]
