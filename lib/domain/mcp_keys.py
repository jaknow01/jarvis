"""Self-service MCP API keys — the ones the /mcp/join page issues (see docs/MCP.md).

One active key per FPL entry: issuing a new key for an entry revokes its previous one, so
a friend who lost their key just asks for a new one. Only SHA-256 hashes are stored.
Hand-managed keys (Jarvis, Claude Code, exceptions) stay in ``config/mcp_clients.yaml``.

Persists to Postgres when ``DATABASE_URL`` is set (``app/db/mcp_key_repo.py``), else to
``data/mcp_data/keys.json`` — the same backend-selection pattern as the other stores.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MCP_KEYS_PATH = Path("data/mcp_data/keys.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class McpKeyStore:
    """On-disk JSON backend: ``{"keys": [row, …]}``; atomic writes, lock-guarded."""

    def __init__(self, path: Path = MCP_KEYS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"keys": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"mcp keys: could not read {self.path}: {e}")
            return {"keys": []}
        return data if isinstance(data, dict) and isinstance(data.get("keys"), list) else {"keys": []}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def issue(self, *, key_sha256: str, entry_id: int, league_id: Optional[int], manager_name: str,
              team_name: str, servers: list[str]) -> int:
        with self._lock:
            data = self._load()
            replaced = 0
            for row in data["keys"]:
                if row.get("entry_id") == entry_id and not row.get("revoked_at"):
                    row["revoked_at"] = _now()
                    replaced += 1
            data["keys"].append({
                "key_sha256": key_sha256, "entry_id": entry_id, "league_id": league_id,
                "manager_name": manager_name, "team_name": team_name, "servers": list(servers),
                "created_at": _now(), "revoked_at": None,
            })
            self._save(data)
            return replaced

    def active(self) -> list[dict]:
        return [r for r in self._load()["keys"] if not r.get("revoked_at")]

    def revoke_entry(self, entry_id: int) -> int:
        with self._lock:
            data = self._load()
            n = 0
            for row in data["keys"]:
                if row.get("entry_id") == entry_id and not row.get("revoked_at"):
                    row["revoked_at"] = _now()
                    n += 1
            if n:
                self._save(data)
            return n


class _PostgresMcpKeyBackend:
    def issue(self, **kwargs) -> int:
        from app.db import mcp_key_repo
        return mcp_key_repo.issue(**kwargs)

    def active(self) -> list[dict]:
        from app.db import mcp_key_repo
        return mcp_key_repo.active()

    def revoke_entry(self, entry_id: int) -> int:
        from app.db import mcp_key_repo
        return mcp_key_repo.revoke_entry(entry_id)


def _select_backend():
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            init_db()
            logger.info("MCP key store backend: Postgres")
            return _PostgresMcpKeyBackend()
    except Exception as e:  # noqa: BLE001 - never let storage selection crash the app
        logger.error(f"Postgres MCP key backend unavailable ({e}); falling back to JSON file")
    logger.info("MCP key store backend: JSON file")
    return McpKeyStore()


class _McpKeyProxy:
    """Delegates to the backend chosen lazily on first use (after .env is loaded)."""

    def __init__(self):
        self._impl = None

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def issue(self, **kwargs) -> int:
        return self._store().issue(**kwargs)

    def active(self) -> list[dict]:
        return self._store().active()

    def revoke_entry(self, entry_id: int) -> int:
        return self._store().revoke_entry(entry_id)


mcp_keys = _McpKeyProxy()
