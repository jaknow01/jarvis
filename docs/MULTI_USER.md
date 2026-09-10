# Multi-user network

Jarvis started as a single-owner assistant. The multi-user rework lets a small,
**semi-trusted** circle (family/friends) use it — each with their own conversation,
memory, and a tailored set of subagents/tools, with physical resources (smart devices,
maps) shared per household. "Semi-trusted" means isolation is about **correctness** (not
mixing people's data), not a hardened tenant boundary: there is no rate-limiting, no
cross-user prompt-injection defense, no per-user key isolation.

## Identity & the whitelist

Every inbound message resolves `(channel, external_id)` → `user_id` via the
`user_identities` table. **That table is the whitelist**: a sender with no row does not
resolve to a user and is ignored (no auto-provisioning). The owner adds identities with
`/user add`. `MESSENGER_ALLOWED_SENDER_ID` is no longer the runtime gate — it is read
once at startup to **seed** the owner's identity (`seed_owner()`), so the pre-existing
single-user deployment keeps working unchanged.

Separating `external_id` (channel-specific) from `user_id` (stable) means the same
person can map to one user across channels later and share memory/profile.

## Users, groups, membership

- `users` — the stable principal (cognition owner).
- `groups` — the shared-resource (household) unit **and** a config layer; carries an
  integer `priority`.
- `user_groups` — many-to-many; a user may belong to **multiple** groups.

The migrated owner is user `owner` in group `default` (see `lib/domain/principals.py`).

## Per-principal profile resolution (roster + models)

Each turn resolves the acting principal's **profile** — which subagents are in the
coordinator's roster, and each agent's `model`/`reasoning`. One rule does it all
(`lib/domain/profile.py`):

> Order the layers `[default, groups sorted by ascending priority, user]` and, per
> `(agent, field)`, take the **last non-null value**.

Everything falls out of that single rule:

- **Union of agents.** `enabled` starts from the env default (`agent_enabled` — True
  unless `AGENT_<NAME>_ENABLED` is falsy). `unset` never disables; only an explicit
  `enabled=false` in a later layer removes an agent. So the roster is the union of agents
  enabled across the user's groups, minus explicit disables.
- **User wins.** The user layer is applied last.
- **Group priority breaks ties.** Groups are applied ascending by `priority`, so the
  higher-priority group is applied later and wins.
- **Empty-config parity.** With no DB config rows, `enabled` is the env default and
  `model`/`reasoning` are `None` — meaning the LLM resolver falls back to
  `AGENT_<NAME>_MODEL` / `DEFAULT_MODEL` exactly as before this change.

The layers live in one table, `agent_config(scope_type ∈ {default,group,user},
scope_id, agent_name, enabled, model, reasoning)`, nullable so "unset" is representable.
`FieldValue.source` records which layer set each winning value (for `/profile`).

The deterministic **composer** runs for every principal; only the **coordinator's**
roster/models vary. Models reach `build_llm` via a per-turn contextvar
(`bind_model_overrides`), so the registry stays the single hook and no per-agent factory
changes.

## Context scopes: group vs user

`Ctx` (`lib/core/cache.py`) is composed of two scopes:

- **Group scope** (`GroupScope`): shared physical resources — devices, device states,
  device preferences, known addresses/maps. Loaded per `group_id` and **cached**
  (`group_scope_for`, TTL `GROUP_SCOPE_TTL_SECONDS`, default 3600s; explicit
  `invalidate_group_scope`). A household reuses one device inventory. The resource group
  for a turn is the user's **highest-priority** group.
- **User scope**: `user_id`, `group_id`, resolved `profile`, `conversation_id`, and
  per-turn scratch (`time_date_now`). Fresh per turn.

Tools keep the same accessor surface (`app_context(ctx).devices`, `.user_id`, …); the
group-resource attributes delegate to the bound `GroupScope`. The per-turn contextvar
binding isolates concurrent turns for different principals by construction.

## Per-user data isolation

- **Memory** (`memory_entries.user_id`): reads/writes and the injected profile are scoped
  to the acting user; there is **no** group/shared memory. Legacy rows with no `user_id`
  belong to the migrated owner. Dedup uniqueness is per user.
- **Conversation history** stays per channel key (`messenger:{psid}`), which is per-user
  in practice — preserving the owner's continuity through the migration.
- **Scheduled jobs** (`scheduled_jobs.user_id`): a job runs as its owner — a proactive
  `origin="system"` turn resolves that user's profile/context/memory and delivers to
  them. Legacy ownerless jobs default to the owner.

## Config: static vs per-principal

Two config systems for two lifetimes:

- **Static app config** (providers, defaults, feature toggles, infra): typed
  `lib/core/settings.py` (`Settings` + `env_flag`/`env_int`/`env_str`). Dependency-free
  by design — **not** `pydantic-settings`, and **not** Hydra — to respect the repo's
  "don't churn the lockfile" rule while still giving a typed, discoverable surface.
- **Per-principal profiles** (rosters/models): **data in the DB** (`agent_config`),
  because they change at runtime via admin commands without a redeploy.

## Owner-only admin commands

Reuse the `/` surface (deterministic, no LLM, owner-gated — the webhook restricts the
admin surface to the owner user):

- `/user list | add <external_id> [name] | assign <user_id> <group,…>`
- `/group list | add <name> [priority] | priority <name> <n> | agents <name> +a -b a=provider/model`
- `/profile <user_id>` — the resolved roster and, per agent field, the winning layer.

## Migration

`python -m app.db.migrate` (Postgres) creates the owner/default group, seeds the owner
identity from `MESSENGER_ALLOWED_SENDER_ID`, and backfills existing `memory_entries` /
`scheduled_jobs` with the owner `user_id`. On startup both the webhook and the REPL call
`seed_owner()` (idempotent), so the JSON backend gets the same owner seed. The migration
is additive and each step is independently reversible; with no per-principal config the
resolved profile equals today's env-driven behavior.
