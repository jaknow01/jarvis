# Storage: Postgres + on-disk JSON

## Where data lives

Jarvis keeps its persistent data in **Postgres** (added to `docker-compose.yml`).
The on-disk JSON files under `data/` are **kept** — they are the easy-to-eyeball
copy and the migration source — but Postgres is the eventual home. Nothing deletes
the JSON files.

| Data | On disk (kept) | Postgres table | Who writes it |
|------|----------------|----------------|---------------|
| Long-term user memory | `data/memory_data/memory.json` | `memory_entries` | `memory_operator` at runtime |
| Smart-light inventory | `data/smart_device_data/smart_devices.json` | `smart_devices` | edited by hand |
| Lighting preferences | `data/smart_device_data/preferences.json` | `device_preferences` (JSONB doc) | edited by hand |
| Maps memory | `data/maps_data/maps_memory.json` | `maps_memory` (JSONB doc) | edited by hand |

## The DB layer lives in `app/db/`

All Postgres code is isolated under `app/db/` (kept out of the `lib/` agents/tools):

- `connection.py` — reads `DATABASE_URL` (at call time, so `.env` is respected),
  yields a connection per unit of work, commit/rollback wrapper.
- `schema.py` — idempotent DDL + `init_db()`.
- `memory_repo.py` — `PostgresMemoryStore`, a drop-in backend with the same API as
  the JSON `MemoryStore`.
- `migrate.py` — one-shot, idempotent, **non-destructive** migration of the JSON
  stores into Postgres.

## How the memory store picks a backend

`lib/memory.py` exposes a `memory` singleton that chooses its backend **lazily on
first use** (after `.env` is loaded):

- `DATABASE_URL` set → `PostgresMemoryStore` (and `init_db()` runs).
- otherwise → the on-disk JSON `MemoryStore`.

So the writer (`memory_operator`) transparently writes to Postgres when configured,
and still works file-only in a dev/test environment with no database. If Postgres is
configured but unreachable, it logs an error and falls back to the JSON store rather
than crashing the assistant.

Only the **memory** store flips to Postgres as its live source today (it is the one
thing agents write at runtime). The device/maps/preferences readers still read their
JSON files for now — that data is migrated into Postgres too, ready for those readers
to switch over later.

## Configuration

`.env` (see `.env_template`):

```dotenv
DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis
```

`docker-compose.yml` runs a `postgres:16` service (user/pass/db all `jarvis`, dev
defaults — change for anything exposed) with a `pgdata` volume and a healthcheck; the
`agent` service gets `DATABASE_URL` pointing at the `postgres` host on the compose
network and waits for it to be healthy.

## Running the migration

```bash
docker compose up -d postgres        # start the database
poetry run python -m app.db.migrate  # copy JSON -> Postgres (idempotent, keeps files)
```
