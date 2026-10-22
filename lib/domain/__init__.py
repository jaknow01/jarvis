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
from lib.domain.observations import (
    observations,
    ObservationsStore,
)
from lib.domain.principals import (
    principals,
    PrincipalStore,
    new_user_id,
    new_group_id,
    seed_owner,
    is_admin,
    OWNER_USER_ID,
    DEFAULT_GROUP_ID,
    DEFAULT_GROUP_NAME,
    ADMINS_GROUP_ID,
    ADMINS_GROUP_NAME,
)
from lib.domain.agent_config import (
    agent_config,
    AgentConfigStore,
    DEFAULT_SCOPE,
    GROUP_SCOPE,
    USER_SCOPE,
    VALID_SCOPES,
)
from lib.domain.profile import (
    resolve_profile,
    Profile,
    AgentProfile,
    FieldValue,
)
from lib.domain.principals_config import (
    apply_config_file,
    load_config_file,
    reconcile,
    config_path,
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
from lib.domain.mcp_keys import mcp_keys, McpKeyStore

__all__ = [
    "conversations",
    "ConversationStore",
    "observations",
    "ObservationsStore",
    "principals",
    "PrincipalStore",
    "new_user_id",
    "new_group_id",
    "seed_owner",
    "is_admin",
    "OWNER_USER_ID",
    "DEFAULT_GROUP_ID",
    "DEFAULT_GROUP_NAME",
    "ADMINS_GROUP_ID",
    "ADMINS_GROUP_NAME",
    "agent_config",
    "AgentConfigStore",
    "DEFAULT_SCOPE",
    "GROUP_SCOPE",
    "USER_SCOPE",
    "VALID_SCOPES",
    "resolve_profile",
    "Profile",
    "AgentProfile",
    "FieldValue",
    "apply_config_file",
    "load_config_file",
    "reconcile",
    "config_path",
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
    "mcp_keys",
    "McpKeyStore",
]
