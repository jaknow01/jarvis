## Why

Jarvis is currently a single-owner assistant: identity is a single Messenger
allowlist entry, the coordinator's subagent roster is hardcoded and toggled by
global env vars, and all state (memory, devices, addresses) lives in one shared
`Ctx` singleton and un-scoped stores. To let a small, semi-trusted circle
(family/friends) use it — each with their own conversation, memory, and a tailored
set of subagents/tools, possibly shared per household — the network must become
principal-aware end to end, without a heavyweight multi-tenant security posture.

## What Changes

- **Identity & whitelist.** Inbound messages resolve `(channel, external_id)` →
  `user_id` via a `user_identities` table. An external id absent from the table is
  ignored (no auto-provisioning). **BREAKING**: `MESSENGER_ALLOWED_SENDER_ID` stops
  being the access gate; it is migrated into `user_identities` as the owner's row.
- **Users & groups.** New `users`, `groups` (with `priority`), and a many-to-many
  `user_groups`. A user can belong to multiple groups.
- **Per-principal roster & model config.** The coordinator's subagent set and each
  agent's model/reasoning are resolved per principal from three layers —
  `defaults → groups (ascending priority) → user` — by one rule: *last-non-null
  wins per field*. Union of agents emerges because `unset` never disables (only an
  explicit `enabled=false` does); the user layer wins; group `priority` breaks ties.
  The deterministic composer is unchanged and runs for every user; only the
  coordinator's roster/models vary. Global `AGENT_<NAME>_ENABLED` is superseded by
  the `default` layer of `agent_config`.
- **Principal-scoped context.** `Ctx` splits into a **group scope** (physical/shared
  resources: devices, device preferences, known addresses, maps — loaded per
  `group_id`, cacheable) and a **user scope** (memory, conversation history +
  compression, resolved profile — per `user_id`, fresh per turn). The per-turn
  contextvar binding carries the composed, principal-scoped `Ctx`.
- **Per-user data isolation.** `memory_entries` and `scheduled_jobs` gain `user_id`;
  memory is per-user only. Conversation history stays per-channel and is keyed via
  the resolved user. Proactive (`origin="system"`) scheduler turns carry `user_id`
  so they load the right profile, `Ctx`, and memory.
- **Config organization.** Static app config moves to typed Pydantic Settings;
  per-principal profiles are DB-backed (`agent_config` with
  `scope_type ∈ {default, group, user}`). The layered merge may use OmegaConf
  standalone — explicitly **not** Hydra.
- **Owner-only admin surface.** Reuse the `/` command surface for user/group
  management: `/user add|list|assign`, `/group ...`, and `/profile <user>` showing
  layer-by-layer roster/model resolution for debugging.
- **Migration.** The current single-user setup keeps working: today's owner becomes
  a `user` in a `default` group, seeded from `MESSENGER_ALLOWED_SENDER_ID`; with no
  per-principal config present, the resolved profile equals today's env-driven
  behavior.

## Capabilities

### New Capabilities

- `user-identity`: resolving an inbound `(channel, external_id)` to a `user_id`
  through a manual whitelist, and rejecting unknown senders.
- `user-groups`: the users/groups/membership data model (multi-group, group
  `priority`) and the owner-only admin commands that manage it.
- `agent-roster-resolution`: the three-layer profile resolution producing a
  per-principal coordinator roster and per-agent model/reasoning config.
- `principal-scoped-context`: composing the per-turn `Ctx` from a cacheable group
  scope (shared physical resources) and a per-user cognitive scope.
- `per-user-data-isolation`: scoping long-term memory, conversation history, and
  scheduled jobs to a `user_id`, including proactive system-originated turns.

### Modified Capabilities

- `model-providers`: the "per-agent model configuration with defaults" requirement
  changes — an agent's model/reasoning is now resolved from the current principal's
  layered profile, with `AGENT_<NAME>_MODEL` / `DEFAULT_MODEL` backing the `default`
  layer for backward compatibility.

## Impact

- **Code:** `lib/runtime/engine.py` (thread `user_id`, build principal `Ctx`),
  `app/webhook.py` (identity resolution + whitelist gate), `lib/core/cache.py`
  (`Ctx` split into group/user scopes), `lib/core/registry.py` + `lib/core/llm.py`
  (profile-aware LLM resolution), `lib/agents/coordinator/agent.py`
  (`create_coordinator_agent(profile)`), `lib/domain/memory.py`,
  `lib/domain/scheduler.py` + `lib/runtime/scheduler_runner.py` (user scoping),
  `lib/runtime/admin.py` (new commands).
- **Data / DB:** new tables `users`, `user_identities`, `groups`, `user_groups`,
  `agent_config`; `user_id` added to `memory_entries` and `scheduled_jobs`; a
  migration seeding the owner + default group. `app/db/` gains repos for the new
  tables.
- **Dependencies:** add `pydantic-settings` (and optionally `omegaconf`); no removals.
- **Config:** `MESSENGER_ALLOWED_SENDER_ID` deprecated as a gate (migrated);
  `AGENT_<NAME>_ENABLED` / `AGENT_<NAME>_MODEL` become the `default` layer.
- **Docs:** new `docs/MULTI_USER.md`; updates to `docs/ADMIN.md`, `docs/STORAGE.md`,
  `docs/MEMORY.md`, `docs/SCHEDULER.md`.
