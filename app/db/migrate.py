"""
One-shot migration: copy the on-disk JSON data stores into Postgres.

Idempotent (uses upserts), and **non-destructive** — the JSON files on disk are left
untouched (they remain the easy-to-eyeball copy for now; Postgres is the eventual
home). Run it against a database given by ``DATABASE_URL``:

    poetry run python -m app.db.migrate
    # or
    python app/db/migrate.py
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from psycopg.types.json import Json

from app.db.connection import connection, database_url
from app.db.schema import init_db

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_JSON = REPO_ROOT / "data" / "memory_data" / "memory.json"
DEVICES_JSON = REPO_ROOT / "data" / "smart_device_data" / "smart_devices.json"
PREFERENCES_JSON = REPO_ROOT / "data" / "smart_device_data" / "preferences.json"
MAPS_JSON = REPO_ROOT / "data" / "maps_data" / "maps_memory.json"
CONVERSATIONS_JSON = REPO_ROOT / "data" / "conversation_data" / "conversations.json"


def _load(path: Path):
    if not path.exists():
        print(f"  - {path.name}: not found, skipping")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  - {path.name}: could not read ({e}), skipping")
        return None


def _parse_dt(value) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def migrate_memory(conn) -> int:
    data = _load(MEMORY_JSON)
    if not data:
        return 0
    entries = data.get("entries", []) if isinstance(data, dict) else []
    n = 0
    for e in entries:
        conn.execute(
            """
            INSERT INTO memory_entries (id, text, category, source, confidence, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                text = EXCLUDED.text, category = EXCLUDED.category, source = EXCLUDED.source,
                confidence = EXCLUDED.confidence, updated_at = EXCLUDED.updated_at
            """,
            (
                e.get("id") or ("mem_" + str(n)),
                e.get("text", ""),
                e.get("category", "preferences"),
                e.get("source", "user"),
                e.get("confidence", "high"),
                _parse_dt(e.get("created_at")),
                _parse_dt(e.get("updated_at")),
            ),
        )
        n += 1
    return n


def migrate_devices(conn) -> int:
    data = _load(DEVICES_JSON)
    if not data:
        return 0
    elements = data.get("list_of_elements", []) if isinstance(data, dict) else []
    n = 0
    for el in elements:
        p = el.get("params", {})
        conn.execute(
            """
            INSERT INTO smart_devices (dev_id, custom_name, room, zones, local_ip, local_key, version, params)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dev_id) DO UPDATE SET
                custom_name = EXCLUDED.custom_name, room = EXCLUDED.room, zones = EXCLUDED.zones,
                local_ip = EXCLUDED.local_ip, local_key = EXCLUDED.local_key,
                version = EXCLUDED.version, params = EXCLUDED.params
            """,
            (
                p.get("id"),
                el.get("custom_name"),
                p.get("room"),
                Json(p.get("zones", [])),
                p.get("local_ip"),
                p.get("local_key"),
                str(p.get("version", "3.3")),
                Json(p),
            ),
        )
        n += 1
    return n


def migrate_conversations(conn) -> int:
    """Copy any on-disk JSON conversations + their messages into Postgres (idempotent).

    Conversations start fresh post-migration, so this is usually a no-op; it exists so
    a dev who accumulated JSON history before configuring Postgres keeps it."""
    data = _load(CONVERSATIONS_JSON)
    if not data:
        return 0
    convs = data.get("conversations", {}) if isinstance(data, dict) else {}
    n = 0
    for channel_key, conv in convs.items():
        conn.execute(
            """
            INSERT INTO conversations (id, channel_key, created_at, last_active_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (channel_key) DO NOTHING
            """,
            (
                conv.get("id") or str(__import__("uuid").uuid4()),
                channel_key,
                _parse_dt(conv.get("created_at")),
                _parse_dt(conv.get("last_active_at")),
            ),
        )
        row = conn.execute(
            "SELECT id FROM conversations WHERE channel_key = %s", (channel_key,)
        ).fetchone()
        conv_id = row[0]
        for m in conv.get("messages", []):
            conn.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, turn_index,
                                      created_at, mlflow_trace_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    m.get("id") or str(__import__("uuid").uuid4()),
                    conv_id,
                    m.get("role", "user"),
                    m.get("content", ""),
                    m.get("turn_index", 0),
                    _parse_dt(m.get("created_at")),
                    m.get("mlflow_trace_id", ""),
                ),
            )
        n += 1
    return n


def seed_owner_and_backfill(conn) -> tuple[int, int]:
    """Seed the owner user + default group (from MESSENGER_ALLOWED_SENDER_ID) and backfill
    existing memory/scheduled-job rows with the owner ``user_id``. Idempotent.

    Returns ``(memory_rows_backfilled, job_rows_backfilled)``. Uses the SAME stable ids
    the runtime resolves the owner to (``OWNER_USER_ID`` / ``DEFAULT_GROUP_*``), so
    backfilled rows line up with live per-user scoping.
    """
    import os
    from lib.domain.principals import (
        OWNER_USER_ID, DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME,
    )

    conn.execute(
        "INSERT INTO groups (id, name, priority) VALUES (%s, %s, 0) "
        "ON CONFLICT (name) DO NOTHING",
        (DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME),
    )
    conn.execute(
        "INSERT INTO users (id, display_name, status) VALUES (%s, %s, 'active') "
        "ON CONFLICT (id) DO NOTHING",
        (OWNER_USER_ID, "Owner"),
    )
    conn.execute(
        "INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (OWNER_USER_ID, DEFAULT_GROUP_ID),
    )
    allowed = (os.getenv("MESSENGER_ALLOWED_SENDER_ID") or "").strip()
    if allowed:
        conn.execute(
            "INSERT INTO user_identities (user_id, channel, external_id) VALUES (%s, 'messenger', %s) "
            "ON CONFLICT (channel, external_id) DO NOTHING",
            (OWNER_USER_ID, allowed),
        )

    mem = conn.execute(
        "UPDATE memory_entries SET user_id = %s WHERE user_id IS NULL", (OWNER_USER_ID,)
    ).rowcount
    jobs = conn.execute(
        "UPDATE scheduled_jobs SET user_id = %s WHERE user_id IS NULL", (OWNER_USER_ID,)
    ).rowcount
    return mem, jobs


def _migrate_document(conn, table: str, data) -> int:
    if data is None:
        return 0
    conn.execute(
        f"""
        INSERT INTO {table} (name, data, updated_at)
        VALUES ('default', %s, now())
        ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
        """,
        (Json(data),),
    )
    return 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    if not database_url():
        print("error: DATABASE_URL is not set (nothing to migrate into).")
        return 2

    print(f"Migrating on-disk JSON stores into Postgres ({database_url().split('@')[-1]})")
    init_db()
    with connection() as conn:
        mem = migrate_memory(conn)
        dev = migrate_devices(conn)
        prefs = _migrate_document(conn, "device_preferences", _load(PREFERENCES_JSON))
        maps = _migrate_document(conn, "maps_memory", _load(MAPS_JSON))
        convs = migrate_conversations(conn)
        mem_bf, jobs_bf = seed_owner_and_backfill(conn)

    print("\nMigration complete (JSON files left untouched):")
    print(f"  memory_entries      : {mem} row(s)")
    print(f"  smart_devices       : {dev} row(s)")
    print(f"  device_preferences  : {prefs} document(s)")
    print(f"  maps_memory         : {maps} document(s)")
    print(f"  conversations       : {convs} conversation(s)")
    print(f"  owner/default seed  : memory user_id backfilled {mem_bf}, jobs {jobs_bf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
