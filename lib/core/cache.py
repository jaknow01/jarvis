# import aioredis
import contextvars
import os
from typing import Any
import redis.asyncio as redis

# The shared per-run Ctx is carried on a contextvar for the duration of a turn
# rather than on the LlamaIndex workflow Context (design D3, "carriage to confirm
# during apply"). Two Context-native options were tried and rejected during apply:
#   - ctx.store: the workflow *serializes* its store, and our Ctx (Redis client,
#     live tinytuya device handles) is not serializable, so it raises.
#   - a plain attribute on the Context object: the workflow hands each step/tool a
#     *different* Context wrapper (only ctx.store crosses that boundary), so an
#     attribute set before the run never reaches the tools.
# A contextvar sidesteps both: it is set once per turn in engine.handle_message and
# propagates to every child asyncio task the workflow spawns (coordinator step
# workers and nested sub-agent runs), so every tool in the turn sees the same Ctx.
_CURRENT_CTX: "contextvars.ContextVar[Ctx]" = contextvars.ContextVar("jarvis_app_ctx")


class Cache():
    def __init__(self, ttl:int = 3600 * 24 * 5):
        # Host runs default to localhost; inside docker-compose the app sets
        # REDIS_URL=redis://redis:6379 (service name). See docker-compose.yml.
        self.redis_cache = redis.from_url(
            url = os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses = True
        )
        self.ttl = ttl

    async def save_to_cache(self, key: str, val: Any):
        """Save to redis with a given key"""
        await self.redis_cache.set(key, val)

    async def get_from_cache(self, key: str) -> Any:
        """Retrieves information from cache"""
        data = await self.redis_cache.get(key)
        return data

class Ctx():
    def __init__(self, cache: Cache = Cache()):
        self.cache = cache
        self.devices_preferences = {}

        self.devices_states: dict[str, dict] = {}
        # Values are lib.agents.iot_operator.device.SmartDevice at runtime, typed as
        # Any so `core` stays a leaf and never imports the iot domain (DAG: up-only).
        self.devices: dict[str, Any] = {}
        self.known_adresses: dict = {}
        self.time_date_now: dict = {}
        # The id scoping the current turn's conversation (e.g. "repl" or
        # "messenger:{psid}"). Set by lib.runtime.engine.handle_message so tools —
        # notably the scheduler — know which channel/target to deliver a proactive
        # reply to.
        self.conversation_id: str | None = None


def bind_app_context(app: "Ctx") -> "contextvars.Token":
    """Bind ``app`` as the shared run context for the current turn.

    Called once by ``engine.handle_message`` before running the coordinator; the
    binding propagates to every child asyncio task the workflow spawns. Returns the
    contextvar ``Token`` so the caller may reset it after the turn.
    """
    return _CURRENT_CTX.set(app)


def reset_app_context(token: "contextvars.Token") -> None:
    """Undo a previous ``bind_app_context`` (best-effort; ignores stale tokens)."""
    try:
        _CURRENT_CTX.reset(token)
    except (ValueError, LookupError):
        pass


def app_context(ctx=None) -> "Ctx":
    """Retrieve the shared per-run ``Ctx`` for the current turn.

    Every function-tool reads/writes the shared run state through this accessor:

        app = app_context(ctx)
        app.time_date_now = ...            # write scratch state
        devices = app.devices              # read shared state
        await app.cache.get_from_cache(k)  # Redis handle

    The ``ctx`` argument is the LlamaIndex workflow ``Context`` the tool receives
    (auto-injected by annotation, hidden from the model's tool schema). It is
    accepted for signature symmetry with the old ``RunContextWrapper[Ctx]`` and
    kept available for future Context-native use; the shared ``Ctx`` itself is
    resolved from the per-turn contextvar so state written by one tool is visible
    to any later tool in the same turn.
    """
    return _CURRENT_CTX.get()
