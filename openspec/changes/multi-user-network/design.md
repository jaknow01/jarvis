## Context

Jarvis is a single-owner multi-agent assistant. Today:

- Identity is a single Messenger allowlist (`MESSENGER_ALLOWED_SENDER_ID` in
  `app/webhook.py`); everything else is implicitly the owner.
- The coordinator's subagent list is hardcoded in
  `lib/agents/coordinator/agent.py` and filtered by global env toggles
  (`agent_enabled()` in `lib/core/registry.py`).
- A single shared `Ctx` singleton (`app.state.ctx`, `lib/core/cache.py`) carries
  devices/preferences/addresses and is reused across all senders; it is bound
  per-turn via a contextvar.
- Per-agent model/reasoning resolve from `AGENT_<NAME>_MODEL` / `DEFAULT_MODEL`
  through `build_llm` (`lib/core/llm.py`), bound into the `LLM_BY_AGENT` registry.
- Conversation history (`lib/domain/conversation.py`) is already keyed per channel
  (`messenger:{psid}`), including the rolling summary. Memory
  (`lib/domain/memory.py`, table `memory_entries`) is global/un-scoped. Scheduled
  jobs (`scheduled_jobs`) carry `conversation_id`/`target` but no owner.

The goal is to serve a small, **semi-trusted** circle (family/friends): correctness
isolation between users, not a hardened multi-tenant security boundary. Devices are a
**household (group) resource**; cognition (memory/history) is **per user**. Users may
belong to **multiple groups**, and each user's subagent roster + models are resolved
from layered config.

Constraints: preserve the strict `runtime → agents → {domain, core}` import DAG;
registration-by-import-time-side-effect must keep working; the current single-user
deployment must not break at any point; don't churn the lockfile casually.

## Goals / Non-Goals

**Goals:**

- Resolve inbound `(channel, external_id)` → `user_id` via a manual whitelist; ignore
  unknown senders.
- Users, groups (with `priority`), and many-to-many membership.
- Per-principal coordinator roster + per-agent model/reasoning from a three-layer
  merge (`default → groups by priority → user`) using one rule (last-non-null-wins;
  union via unset≠disable; user wins; priority breaks group ties).
- Split `Ctx` into a cacheable **group scope** (physical/shared resources) and a
  per-turn **user scope** (memory, history, profile).
- Scope memory and scheduled jobs by `user_id`; proactive `origin="system"` turns run
  under the owning user's profile/context.
- Typed static config (Pydantic Settings); DB-backed per-principal profiles.
- Owner-only `/` admin commands for user/group management and profile inspection.
- A migration that keeps today's owner fully working with zero manual config.

**Non-Goals:**

- No hardened multi-tenant security (rate limiting, cross-user prompt-injection
  defense, per-user API-key isolation, quota enforcement). Semi-trusted only.
- No auto-provisioning / self-signup of users.
- No group-level or shared long-term memory (per-user only this pass).
- No new messaging channel; Messenger + REPL remain the transports.
- No replacement of the composer or the coordinator/subagent execution model.
- Not adopting Hydra or a config framework with a launch-time `@main` entry point.

## Decisions

### D1. Identity: `user_identities` whitelist keyed by `(channel, external_id)`

Resolve identity at the transport boundary (`app/webhook.py`), before
`handle_message`. A miss is ignored exactly like today's allowlist miss. Separating
`external_id` (channel-specific) from `user_id` (stable) lets the same person map to
one user across future channels and share memory/profile.

- *Alternative considered:* keep `conversation_id = messenger:{psid}` as the identity.
  Rejected — it conflates channel with person and can't unify a user across channels,
  and gives nothing to hang groups/profile off.

### D2. Data model: five new tables + `user_id` on two existing

```
users(id, display_name, status, created_at)
user_identities(user_id, channel, external_id)      UNIQUE(channel, external_id)
groups(id, name, priority)
user_groups(user_id, group_id)                       PK(user_id, group_id)
agent_config(scope_type, scope_id, agent_name, enabled, model, reasoning)
             scope_type ∈ {default, group, user}; scope_id → group/user (NULL for default)
memory_entries         += user_id     (unique index → (user_id, lower(text), category))
scheduled_jobs         += user_id
```

One `agent_config` table holds all three layers, discriminated by `scope_type`.
Resolution reads the `default` rows + the user's groups' rows + the user's rows and
merges them. `enabled`, `model`, `reasoning` are nullable so `unset` is representable
(critical for the union semantics).

- *Alternative considered:* a table per layer (`default_config`, `group_config`,
  `user_config`). Rejected — three near-identical schemas and three repos for one
  concept; the merge is cleaner reading a single shaped table.

### D3. Resolution: one rule for roster + models

Order the layers `[default, groups sorted by ascending priority, user]`; per
`(agent, field)` take the last non-null value. Roster = agents whose merged `enabled`
is truthy. Union emerges for free because `unset` never turns anything off — only an
explicit `enabled = false` in a later layer removes an agent. This collapses "union",
"priority tiebreak" and "user wins" into a single deterministic pass, and makes the
`/profile` inspector trivial (record which layer set each winning field).

- *Alternative considered:* separate set-algebra for the roster (union of enabled
  minus disabled) and a different merge for config. Rejected — two mechanisms to keep
  consistent; the single last-non-null pass already yields the same result.

### D4. `Ctx` splits into group scope + user scope

`Ctx` becomes a composition: a **group scope** object (devices, `devices_states`,
`devices_preferences`, `known_adresses`, maps) loaded per `group_id` and cacheable
across turns/users of that group; and a **user scope** (`user_id`, memory handle,
conversation key, resolved profile) built fresh per turn. The engine composes both and
binds the result on the existing contextvar, so tool signatures
(`app_context(ctx).devices`, etc.) are unchanged. Because the contextvar already
isolates per-turn and propagates to step-workers/subagents, concurrent turns for
different principals stay isolated by construction.

- Since it's semi-trusted, a group-scope cache leak between groups would be a bug, not
  a breach — but the cache is keyed strictly by `group_id`, and the user scope is
  never cached.
- *Alternative considered:* keep one shared `Ctx` and namespace every field by
  user/group internally. Rejected — leak-prone, and every tool would need to thread a
  scope key.

### D5. Profile-aware model resolution via the contextvar, not a new registry

Keep `LLM_BY_AGENT` / `build_llm` as the public surface. Put the resolved profile on a
contextvar (bound per turn, like `Ctx`); `build_llm(agent_name)` consults it and falls
back to env (the `default` layer) when absent. This keeps the registry the single hook
and needs no per-agent factory rewrites. `create_coordinator_agent(profile)` takes the
profile explicitly for the roster; models flow through the resolver.

- *Alternative considered:* pass model strings down through every factory signature.
  Rejected — invasive across all `create_*_agent()` and fights the existing resolver.

### D6. Config: Pydantic Settings for static, DB for per-principal — not Hydra

Static app config (provider keys, infra, defaults, feature flags) moves to typed
`pydantic-settings`, replacing scattered `os.getenv` + hand-rolled falsy parsing.
Per-principal profiles are **data in the DB** because they must change without a
redeploy. Hydra targets launch-time composition with a global `@hydra.main` entry
point and multirun sweeps — a poor fit for per-request, per-user runtime resolution in
a long-running server. The layered dict merge itself is ~40 lines; OmegaConf (Hydra's
engine, usable standalone) is an optional convenience for the merge only.

- *Alternatives considered:* Hydra (rejected — launch-time model, overkill); pure env
  vars for everything (rejected — can't express groups/multi-user without a redeploy);
  YAML files per user (rejected — profiles change at runtime via admin commands; DB is
  the natural store, though YAML seeds remain possible).

### D7. Admin surface reused for management

Add owner-only deterministic commands to `lib/runtime/admin.py`: `/user add|list|
assign`, `/group add|priority|agents`, `/profile <user>`. This surface already bypasses
the agents and is owner-gated, and is reachable from the phone.

## Risks / Trade-offs

- **Coordinator rebuilt per turn with per-user models** → the build already happens
  each turn; add a small per-(profile-hash) memo if rebuild cost shows up. Correctness
  first, optimize later.
- **Group-scope cache staleness** (device inventory edited) → key the cache by
  `group_id` with a TTL and an explicit invalidation hook from the admin device
  commands; stale shared resources are low-harm for a household.
- **`origin="system"` turns must carry `user_id`** → jobs without an owner (legacy
  rows) → backfill them to the owner during migration; the runner refuses to run an
  ownerless job rather than guessing.
- **Cross-user leakage via a missed scope** (semi-trusted, so a bug not a breach) →
  centralize scoping in the engine's `Ctx` composition and the store method
  signatures (every memory/job read takes `user_id`); add tests asserting isolation.
- **Migration ordering** → additive schema first (nullable `user_id`, new tables),
  backfill, then flip the gate to `user_identities`. Each step is independently
  reversible.
- **Two config systems** (Pydantic Settings + DB profiles) → accept the split; they
  cover genuinely different lifetimes (deploy-time vs runtime). Document the boundary
  in `docs/MULTI_USER.md`.

## Migration Plan

1. **Additive schema.** Create `users`, `user_identities`, `groups`, `user_groups`,
   `agent_config`; add nullable `user_id` to `memory_entries` and `scheduled_jobs`.
   `init_db()` stays idempotent (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT
   EXISTS`).
2. **Seed owner + default group.** Create the owner user, a `default` group, membership,
   and a `user_identities` row from `MESSENGER_ALLOWED_SENDER_ID`. Backfill existing
   `memory_entries` / `scheduled_jobs` with the owner `user_id`. Existing
   `AGENT_<NAME>_ENABLED` / `AGENT_<NAME>_MODEL` remain the `default` layer (no
   `agent_config` rows needed for parity).
3. **Read path, dual-safe.** Add identity resolution + profile resolution behind the
   scenes while behavior stays identical for the owner (empty group/user layers →
   env-equivalent profile). Verify the owner's turns are unchanged.
4. **Flip the gate.** Switch the webhook access check from `MESSENGER_ALLOWED_SENDER_ID`
   to `user_identities`; deprecate the env var as a gate (still read once for seeding).
5. **Enable multi-user.** Ship the admin commands; add real users/groups and
   per-group `agent_config`.

**Rollback:** each step is additive or a flag flip. Reverting step 4 restores the env
allowlist; the new tables/columns are inert if unused.

## Open Questions

- Group-scope cache invalidation: TTL only, or explicit bust from device admin
  commands (or both)?
- Should `/profile <user>` also allow a **dry-run** of a proposed group/user config
  before it's written?
- Do we want per-user REPL identity (a `--user` flag) for local testing, or is REPL
  always the owner?
- Is a lightweight per-user **rate/So-what** limit worth adding even in the
  semi-trusted model, to bound accidental cost from a chatty member?
