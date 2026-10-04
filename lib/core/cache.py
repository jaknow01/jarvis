# import aioredis
import contextvars
import os
import threading
import time
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

class GroupScope():
    """Shared, physical/household resources that belong to a *group*, not a single user.

    Smart devices, their live states and preferences, and known addresses/maps are the
    same for everyone in a household, so they are loaded once per ``group_id`` and reused
    across that group's users/turns (see :func:`group_scope_for`). Split out of ``Ctx``
    so the per-user turn context (memory, conversation, profile) can be fresh while these
    stay cached. ``devices`` values are ``SmartDevice`` at runtime, typed ``Any`` so
    ``core`` stays a leaf (DAG: up-only)."""

    def __init__(self):
        self.devices: dict[str, Any] = {}
        self.devices_states: dict[str, dict] = {}
        self.devices_preferences: dict = {}
        self.known_adresses: dict = {}


class Ctx():
    """Per-turn shared run context, composed of a (cacheable) group scope and per-user
    state. Tools reach it via ``app_context(ctx)``; the group-resource attributes
    (``devices`` etc.) delegate to the bound :class:`GroupScope` so a household shares
    them, while ``user_id`` / ``conversation_id`` / ``profile`` / ``time_date_now`` are
    per-turn/per-user."""

    def __init__(self, cache: Cache = None, group_scope: "GroupScope | None" = None,
                 user_id: str | None = None, group_id: str | None = None,
                 conversation_id: str | None = None, profile: Any = None):
        self.cache = cache if cache is not None else Cache()
        self._group = group_scope if group_scope is not None else GroupScope()
        # Per-user / per-turn state.
        self.user_id: str | None = user_id
        self.group_id: str | None = group_id
        self.profile: Any = profile  # resolved lib.domain.profile.Profile (Any: DAG)
        self.time_date_now: dict = {}
        # The id scoping the current turn's conversation (e.g. "repl" or
        # "messenger:{psid}"). Set by lib.runtime.engine.handle_message so tools —
        # notably the scheduler — know which channel/target to deliver a proactive
        # reply to.
        self.conversation_id: str | None = conversation_id
        # Coordinator bookkeeping for the turn: names of the sub-agents actually run
        # (appended by agent_as_tool) and the category the coordinator declared via its
        # `no_data_needed` escape tool, if any. The engine uses both to decide whether the
        # coordinator's summary may reach the composer (see engine.handle_message).
        self.subagent_calls: list[str] = []
        self.no_data_kind: str | None = None

    # -- group-scope delegation (keeps the tool-facing attribute surface unchanged) --
    @property
    def devices(self) -> dict[str, Any]:
        return self._group.devices

    @devices.setter
    def devices(self, value: dict) -> None:
        self._group.devices = value

    @property
    def devices_states(self) -> dict[str, dict]:
        return self._group.devices_states

    @devices_states.setter
    def devices_states(self, value: dict) -> None:
        self._group.devices_states = value

    @property
    def devices_preferences(self) -> dict:
        return self._group.devices_preferences

    @devices_preferences.setter
    def devices_preferences(self, value: dict) -> None:
        self._group.devices_preferences = value

    @property
    def known_adresses(self) -> dict:
        return self._group.known_adresses

    @known_adresses.setter
    def known_adresses(self, value: dict) -> None:
        self._group.known_adresses = value


# -- group-scope cache (keyed by group_id, TTL'd, explicitly invalidatable) ----------
_GROUP_SCOPES: "dict[str, tuple[float, GroupScope]]" = {}
_GROUP_LOCK = threading.Lock()


def _group_scope_ttl() -> float:
    """TTL (seconds) for a cached group scope. ``GROUP_SCOPE_TTL_SECONDS``; default 3600.
    0 disables caching (a fresh scope per turn). Shared household resources change rarely,
    so a stale scope is low-harm and the admin device commands invalidate explicitly."""
    from lib.core.settings import env_int
    return float(env_int("GROUP_SCOPE_TTL_SECONDS", 3600, minimum=0))


def group_scope_for(group_id: str | None) -> "GroupScope":
    """Return the cached :class:`GroupScope` for ``group_id`` (creating one on miss),
    or a fresh, uncached scope when ``group_id`` is None or caching is disabled."""
    ttl = _group_scope_ttl()
    if not group_id or ttl <= 0:
        return GroupScope()
    now = time.monotonic()
    with _GROUP_LOCK:
        hit = _GROUP_SCOPES.get(group_id)
        if hit is not None and hit[0] > now:
            return hit[1]
        scope = GroupScope()
        _GROUP_SCOPES[group_id] = (now + ttl, scope)
        return scope


def invalidate_group_scope(group_id: str | None = None) -> None:
    """Drop a cached group scope (or all of them when ``group_id`` is None). Called by the
    admin device/config commands when shared resources change."""
    with _GROUP_LOCK:
        if group_id is None:
            _GROUP_SCOPES.clear()
        else:
            _GROUP_SCOPES.pop(group_id, None)


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
