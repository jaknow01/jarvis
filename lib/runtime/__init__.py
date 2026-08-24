"""runtime layer — the top of the import DAG: entry points and channel adapters that
wire the agents network to a transport (REPL, Messenger webhook) and run scheduled jobs.

Imports go up only: runtime -> agents -> {domain, core}. This `__init__` is the public
surface consumed by `app/`.
"""

from lib.runtime.engine import handle_message
from lib.runtime.chatbot import Chatbot
from lib.runtime.hooks import LoggingRunHooks
from lib.runtime.run_config import Config
from lib.runtime.tracing import setup_tracing
from lib.runtime.scheduler_runner import run_scheduler_loop, scheduler_enabled
from lib.runtime.messenger import (
    iter_message_events,
    send_message,
    verify_challenge,
    verify_signature,
)

__all__ = [
    "handle_message",
    "Chatbot",
    "LoggingRunHooks",
    "Config",
    "setup_tracing",
    "run_scheduler_loop",
    "scheduler_enabled",
    "iter_message_events",
    "send_message",
    "verify_challenge",
    "verify_signature",
]
