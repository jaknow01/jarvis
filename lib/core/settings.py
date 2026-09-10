"""Typed, centralized static app configuration.

A single, dependency-free place for the process-level environment configuration that is
otherwise read via scattered ``os.getenv`` calls with hand-rolled parsing. This is the
"organized config" half of the multi-user rework's config story (the design's D6): the
**static** app config lives here, while **per-principal** config (rosters/models) is data
in the DB (see ``lib/domain/profile.py``). We deliberately avoid a config framework
(no Hydra) and, per the repo's "don't churn the lockfile" rule, avoid adding
``pydantic-settings`` — a plain frozen dataclass with typed accessors gives the same
typed, validated, discoverable surface with zero new dependencies.

Values are read from the environment at ``load()`` time (after ``.env`` is loaded), so
call ``settings()`` where you need a snapshot. The shared parsing helpers
(:func:`env_flag`, :func:`env_int`, :func:`env_str`) are the canonical replacements for
the ad-hoc falsy/int parsing sprinkled across the codebase; adopt them incrementally.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_FALSY = ("0", "false", "no", "off", "disabled")


def env_flag(name: str, default: bool = True) -> bool:
    """A boolean env toggle with the project's standard falsy vocabulary. Unset/empty →
    ``default``; any of 0/false/no/off/disabled (case-insensitive) → False; else True."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in _FALSY


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    """An integer env value; falls back to ``default`` on unset/empty/invalid, clamped to
    ``minimum`` when given."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        val = default
    else:
        try:
            val = int(raw.strip())
        except ValueError:
            val = default
    return max(minimum, val) if minimum is not None else val


def env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    return raw.strip() if (raw is not None and raw.strip() != "") else default


@dataclass(frozen=True)
class Settings:
    """A snapshot of the process-level configuration (read from the environment)."""

    # Models / providers (defaults; per-agent + per-principal overrides layer on top).
    default_model: str | None
    default_reasoning_effort: str | None

    # Storage.
    database_url: str | None
    redis_url: str

    # Feature toggles / loops.
    scheduler_enabled: bool
    tracing_enabled: bool
    progress_ping_enabled: bool
    observations_enabled: bool
    fpl_cache_refresh_enabled: bool
    history_compression_enabled: bool

    # Multi-user.
    group_scope_ttl_seconds: int
    memory_summary_cache_ttl_seconds: int

    # Messenger (transport).
    messenger_allowed_sender_id: str | None

    @staticmethod
    def load() -> "Settings":
        return Settings(
            default_model=env_str("DEFAULT_MODEL") or env_str("OPENAI_DEFAULT_MODEL"),
            default_reasoning_effort=env_str("DEFAULT_REASONING_EFFORT"),
            database_url=env_str("DATABASE_URL"),
            redis_url=env_str("REDIS_URL") or "redis://localhost:6379",
            scheduler_enabled=env_flag("SCHEDULER_ENABLED"),
            tracing_enabled=env_flag("TRACING_ENABLED"),
            progress_ping_enabled=env_flag("PROGRESS_PING_ENABLED"),
            observations_enabled=env_flag("OBSERVATIONS_ENABLED"),
            fpl_cache_refresh_enabled=env_flag("FPL_CACHE_REFRESH_ENABLED"),
            history_compression_enabled=env_flag("HISTORY_COMPRESSION_ENABLED"),
            group_scope_ttl_seconds=env_int("GROUP_SCOPE_TTL_SECONDS", 3600, minimum=0),
            memory_summary_cache_ttl_seconds=env_int("MEMORY_SUMMARY_CACHE_TTL_SECONDS", 60, minimum=0),
            messenger_allowed_sender_id=env_str("MESSENGER_ALLOWED_SENDER_ID"),
        )


def settings() -> Settings:
    """A freshly-loaded configuration snapshot (reads the current environment)."""
    return Settings.load()
