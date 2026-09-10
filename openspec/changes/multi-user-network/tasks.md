## 1. Schema & storage (additive)

- [x] 1.1 Add `users`, `user_identities` (UNIQUE(channel, external_id)), `groups`
  (with `priority`), and `user_groups` (PK(user_id, group_id)) to `app/db/schema.py`
  as idempotent `CREATE TABLE IF NOT EXISTS`
- [x] 1.2 Add `agent_config(scope_type, scope_id, agent_name, enabled, model,
  reasoning)` with nullable config columns; index on `(scope_type, scope_id)`
- [x] 1.3 Add nullable `user_id` to `memory_entries` and `scheduled_jobs`
  (`ADD COLUMN IF NOT EXISTS`); replace `memory_entries` unique index with
  `(user_id, lower(text), category)`
- [x] 1.4 Create `app/db/user_repo.py` (users + identities), `app/db/group_repo.py`
  (groups + membership), `app/db/agent_config_repo.py` (layer rows)
- [x] 1.5 Add JSON-backend equivalents for the new stores (mirror the memory/scheduler
  JSON/Postgres selection pattern) so `DATABASE_URL`-less runs still work

## 2. Migration & seeding

- [x] 2.1 Extend `app/db/migrate.py` to create the owner `user`, a `default` `group`,
  the membership, and a `user_identities` row seeded from
  `MESSENGER_ALLOWED_SENDER_ID` (also `seed_owner()` in principals for both backends)
- [x] 2.2 Backfill existing `memory_entries` and `scheduled_jobs` with the owner
  `user_id`
- [x] 2.3 Verify idempotency: running the migration twice makes no duplicate owner /
  group / identity rows (ON CONFLICT DO NOTHING + upsert/dedupe add_* methods)

## 3. Identity resolution & whitelist gate

- [x] 3.1 Add `resolve_user(channel, external_id) -> user_id | None` (via
  `user_repo`/principals), returning None on a whitelist miss
- [x] 3.2 In `app/webhook.py`, resolve identity before `handle_message`; ignore
  unknown senders; remove `MESSENGER_ALLOWED_SENDER_ID` as the runtime gate (still
  read once for owner seeding); admin surface gated to the owner
- [x] 3.3 Keep the per-channel conversation key (preserves owner continuity) and pass
  `user_id` into `handle_message`

## 4. Profile resolution (roster + models)

- [x] 4.1 Define a `Profile` type (roster set + per-agent `{enabled, model,
  reasoning}`) and a `resolve_profile(user_id, candidate_agents) -> Profile` that reads
  the `default`, the user's groups', and the user's `agent_config` rows
- [x] 4.2 Implement the merge: order `[default, groups by ascending priority, user]`,
  last-non-null-wins per field; roster = agents with truthy merged `enabled`
- [x] 4.3 Record, per winning field, which layer set it (`FieldValue.source`, for
  `/profile` inspection)
- [x] 4.4 Back the `default` layer with the existing env
  (`AGENT_<NAME>_ENABLED` for enabled; `None` model/reasoning ⇒ `build_llm` env
  fallback) so an empty DB reproduces today's behavior
- [x] 4.5 Unit tests: union across groups, explicit `enabled=false` removal, group
  priority tiebreak, user-layer override, unset-does-not-override, empty-config parity

## 5. Principal-scoped context

- [x] 5.1 Split `Ctx` (`lib/core/cache.py`) into a `GroupScope` (devices,
  devices_states, devices_preferences, known_adresses) and per-user state
  (user_id, group_id, conversation_id, resolved profile)
- [x] 5.2 Add a group-scope loader keyed by `group_id` (`group_scope_for`) with a small
  cache + TTL (`GROUP_SCOPE_TTL_SECONDS`) and an explicit `invalidate_group_scope` hook
- [x] 5.3 In `engine.handle_message`, compose the per-turn `Ctx` from the (cached)
  group scope + fresh user scope and bind it on the existing contextvar
- [x] 5.4 Keep `app_context(ctx)` tool-facing accessors working unchanged; test asserts
  no cross-principal bleed between two concurrent turns (`tests/test_principal_context.py`)

## 6. Profile-aware model + coordinator

- [x] 6.1 Put per-agent model/reasoning overrides on a per-turn contextvar
  (`bind_model_overrides`); make `build_llm` (`lib/core/llm.py`) consult it, falling
  back to env when absent
- [x] 6.2 Change `create_coordinator_agent(profile)` to accept the `Profile`; expose only
  rostered subagents as tools; models flow via the bound overrides
- [x] 6.3 Inject the acting user's memory profile (`memory.summary(user_id=…)`) into the
  coordinator prompt; composer still runs for every principal (unchanged)
- [x] 6.4 Retire the global `agent_enabled()` path in the coordinator in favor of the
  resolved roster (env still feeds the `default`/base layer via `resolve_profile`)

## 7. Per-user data isolation

- [x] 7.1 Scope `lib/domain/memory.py` + `app/db/memory_repo.py` reads/writes and
  `summary()` by `user_id`; `memory_operator` tools pass the acting user from
  `app_context`; legacy no-owner rows resolve to the migrated owner
- [x] 7.2 Persist `user_id` on jobs (`build_job`/`build_watch`,
  `app/db/scheduler_repo.py`); list/cancel stay per-conversation (per-user in practice);
  scheduler tools stamp the acting user
- [x] 7.3 In `lib/runtime/scheduler_runner.py`, carry `user_id` on due jobs; run
  `origin="system"` turns under the job owner's resolved profile/context (legacy
  ownerless jobs default to the owner for continuity)
- [x] 7.4 Conversation history + rolling summary remain per user (per-channel key
  `messenger:{psid}` is per-user; unchanged, preserving owner continuity)

## 8. Config system (static)

- [x] 8.1 Add a typed `Settings` dataclass (`lib/core/settings.py`) + shared
  `env_flag`/`env_int`/`env_str` helpers covering current env (providers, defaults,
  feature flags, multi-user knobs). NOTE: per the repo's "don't churn the lockfile"
  rule and the user's decision, this is dependency-free (NOT `pydantic-settings`) — a
  deliberate deviation from design.md D6, delivering the same typed/validated surface.
- [~] 8.2 Shared parsing helpers adopted for new multi-user code (group-scope TTL);
  existing `os.getenv` readers left working and migrated incrementally (legacy env names
  honored). Full sweep intentionally deferred to keep this change low-risk.
- [ ] 8.3 (Optional) `omegaconf` for the layer merge — SKIPPED: the hand-rolled
  last-non-null merge is ~40 lines and clearer; no dependency added.

## 9. Admin surface

- [x] 9.1 Add owner-only `/user add|list|assign` to `lib/runtime/admin.py`
- [x] 9.2 Add owner-only `/group add|priority|agents` (set per-group `agent_config`)
- [x] 9.3 Add `/profile <user>` showing the resolved roster and, per agent field, the
  winning layer (`FieldValue.source`)
- [x] 9.4 `/help` auto-generates from command metadata (new "Użytkownicy" group);
  commands are deterministic (no LLM) and owner-gated (webhook restricts admin to owner)

## 10. Docs & verification

- [x] 10.1 Write `docs/MULTI_USER.md` (identity, groups, layered config, Ctx scopes,
  migration, the static-Settings-vs-DB boundary)
- [x] 10.2 Update `docs/ADMIN.md`, `docs/STORAGE.md`, `docs/MEMORY.md`,
  `docs/SCHEDULER.md`, and `CLAUDE.md` for the multi-user model
- [x] 10.3 End-to-end check: owner unchanged (empty-config parity test + full suite);
  a second user in a different group gets a different roster (admin `/profile` smoke +
  `test_profile`); proactive jobs carry+run under owner; `poetry run pytest` green
  (bar 4 pre-existing OpenAI-key failures unrelated to this change)
