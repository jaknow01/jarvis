"""runtime layer — the top of the import DAG: entry points and channel adapters that
wire the agents network to a transport (REPL, Messenger webhook) and run scheduled jobs.

Imports go up only: runtime -> agents -> {domain, core}. This `__init__` is the public
surface consumed by `app/`.
"""

from lib.runtime.engine import handle_message, ensure_default_group_roster
from lib.runtime.chatbot import Chatbot
from lib.runtime.tracing import setup_tracing, last_trace_id
from lib.runtime.scheduler_runner import run_scheduler_loop, scheduler_enabled
from lib.runtime.fpl_cache_runner import run_fpl_cache_loop, fpl_cache_refresh_enabled
from lib.runtime.observations_runner import run_observations_loop, observations_enabled
from lib.runtime import admin, diag
from lib.runtime.mcp_server import build_mcp_app, EXPOSABLE as MCP_EXPOSABLE
from lib.runtime.mcp_keys import (
    McpClient,
    McpClientRegistry,
    RateLimiter,
    new_key as new_mcp_key,
    hash_key as hash_mcp_key,
)
from lib.runtime.messenger import (
    iter_message_events,
    send_message,
    verify_challenge,
    verify_signature,
)

__all__ = [
    "handle_message",
    "ensure_default_group_roster",
    "Chatbot",
    "setup_tracing",
    "last_trace_id",
    "run_scheduler_loop",
    "scheduler_enabled",
    "run_fpl_cache_loop",
    "fpl_cache_refresh_enabled",
    "run_observations_loop",
    "observations_enabled",
    "admin",
    "diag",
    "build_mcp_app",
    "MCP_EXPOSABLE",
    "McpClient",
    "McpClientRegistry",
    "RateLimiter",
    "new_mcp_key",
    "hash_mcp_key",
    "iter_message_events",
    "send_message",
    "verify_challenge",
    "verify_signature",
]
