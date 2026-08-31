"""Domain layer — shared stateful stores (long-term memory, scheduled jobs).

Sits above `core` and below `agents`/`runtime`: both an agent's tools and the
runtime (engine, scheduler_runner) import these stores from here. Persists to
Postgres when DATABASE_URL is set, else to the on-disk JSON files under data/
(see app/db and docs/STORAGE.md). This `__init__` is the public surface.
"""

from lib.domain.memory import (
    memory,
    MemoryStore,
    summarize,
    VALID_SOURCES,
    VALID_CONFIDENCE,
    SUMMARY_CAP,
    SUGGESTED_CATEGORIES,
)
from lib.domain.conversation import (
    conversations,
    ConversationStore,
)
from lib.domain.scheduler import (
    store,
    build_job,
    build_watch,
    public_view,
    parse_dt,
    describe_schedule,
    next_after,
    is_expired,
    now_tz,
    tz,
    VALID_KINDS,
    VALID_STATUSES,
    VALID_CHANNELS,
)

__all__ = [
    "conversations",
    "ConversationStore",
    "memory",
    "MemoryStore",
    "summarize",
    "VALID_SOURCES",
    "VALID_CONFIDENCE",
    "SUMMARY_CAP",
    "SUGGESTED_CATEGORIES",
    "store",
    "build_job",
    "build_watch",
    "public_view",
    "parse_dt",
    "describe_schedule",
    "next_after",
    "is_expired",
    "now_tz",
    "tz",
    "VALID_KINDS",
    "VALID_STATUSES",
    "VALID_CHANNELS",
]
