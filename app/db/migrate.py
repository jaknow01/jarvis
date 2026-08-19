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

    print("\nMigration complete (JSON files left untouched):")
    print(f"  memory_entries      : {mem} row(s)")
    print(f"  smart_devices       : {dev} row(s)")
    print(f"  device_preferences  : {prefs} document(s)")
    print(f"  maps_memory         : {maps} document(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
