# Long-term memory (`memory_operator`) — design & decisions

The `memory_operator` agent gives Jarvis a **general, cross-cutting long-term
memory** of the owner: durable preferences, facts, habits, interests and routines
that are not tied to a single domain agent. It is the backbone of the project's
"remembers preferences and adapts over time" goal. This document records the design
decided in the memory design session; it is the companion to the code in
`lib/memory.py` + the `memory_operator` tools in `lib/tools.py`.

## Relationship to existing per-agent memory

Some agents already keep their own domain memory as natural-language JSON:
`data/smart_device_data/preferences.json` (lighting) and
`data/maps_data/maps_memory.json` (addresses). Those **stay where they are**, owned
by their agents. `memory_operator` owns a **new, general** store for everything
cross-cutting (e.g. "the user is a fast walker", "prefers Polish", news interests,
personal facts). Consolidating the domain stores into it is possible later but is
out of scope for v1.

## Decisions

### D1 — Structured entries (not bare NL strings)
Each memory is a structured record, so we get provenance, timestamps and de-dup, and
a path to later adaptation/learning:

```json
{
  "id": "mem_1a2b3c4d",
  "text": "The user is a fast walker; on-foot legs take ~1.25x less time than navigation suggests.",
  "category": "habits",
  "source": "user",          // "user" = explicitly stated | "inferred" = assistant concluded it
  "confidence": "high",       // high | medium | low
  "created_at": "2026-08-19T21:40:00",
  "updated_at": "2026-08-19T21:40:00"
}
```

Stored as `{"entries": [ … ]}` in `data/memory_data/memory.json` (git-ignored like
the other data stores; created lazily). Suggested categories: `preferences`,
`facts`, `habits`, `interests`, `routines` — free-form, not enforced.

### D2 — Read path: profile injected into the coordinator prompt **and** a tool
- **Passive personalization:** `create_coordinator_agent()` injects a compact
  memory summary into the coordinator's instructions every turn (the coordinator is
  rebuilt per turn, so it is always fresh). The coordinator uses it to personalize
  and to avoid re-asking what it already knows.
- **On-demand depth:** `get_memory` lets any turn pull the full entries (optionally
  by category) when the summary is not enough.

### D3 — Write path: memory_operator with explicit tools
`memory_operator` is wired into the coordinator as a tool and owns:
`save_memory`, `get_memory`, `update_memory`, `delete_memory`. The coordinator is
instructed to persist a durable preference/fact when the user states one (or when the
assistant reliably infers one, tagged `source="inferred"`). Exact-text duplicates in
the same category refresh the timestamp instead of piling up.

### D4 — v1 scope: preferences & facts only
Reminders / scheduled actions are **deferred** — the scheduler layer does not exist
yet. When it does, `memory_operator` is the natural owner of creating/editing
scheduled entries (per `TODO.txt`), but that is a separate change.

## Tunables / layout

| Thing | Value |
|-------|-------|
| Store file | `data/memory_data/memory.json` (git-ignored, atomic writes) |
| Summary cap | first ~40 entries injected into the coordinator prompt |
| Sources | `user`, `inferred` |
| Confidence | `high`, `medium`, `low` |

## Out of scope (for now)
- Implicit habit *learning* (v1 stores only what is explicitly stated or clearly
  inferred on request; automatic pattern detection is later).
- Reminders / scheduler (D4).
- Migrating the hard-coded "fast walker" assumption out of the maps tool docstring
  into this store (a good early candidate once the store is in use).
- Consolidating the per-agent domain stores into the general store.
