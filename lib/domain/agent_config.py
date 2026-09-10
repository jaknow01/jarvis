"""Layered agent config store (the DB side of per-principal profiles).

Holds config rows for three layers discriminated by ``scope_type``
(``default`` | ``group`` | ``user``). ``scope_id`` is the group/user id (``''`` for the
``default`` layer). Each row's ``enabled`` / ``model`` / ``reasoning`` are nullable so
"unset" is representable — profile resolution (``lib/domain/profile.py``) relies on
unset never disabling an agent.

Storage mirrors the other domain stores: on-disk JSON by default, Postgres
(``app/db/agent_config_repo.py``) when ``DATABASE_URL`` is set.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_CONFIG_PATH = Path("data/principals_data/agent_config.json")

# The three config layers, applied in this order during resolution (default first,
# user last). Group rows sit between them, ordered by group priority at resolve time.
DEFAULT_SCOPE = "default"
GROUP_SCOPE = "group"
USER_SCOPE = "user"
VALID_SCOPES = (DEFAULT_SCOPE, GROUP_SCOPE, USER_SCOPE)

_CONFIG_FIELDS = ("enabled", "model", "reasoning")


def _key(scope_type: str, scope_id: str, agent_name: str) -> str:
    return f"{scope_type}\x1f{scope_id}\x1f{agent_name}"


class AgentConfigStore:
    """On-disk JSON store: a flat list of layer rows."""

    def __init__(self, path: Path = AGENT_CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"rows": []}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"agent_config: could not read {self.path}: {e}")
            return {"rows": []}
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            return {"rows": []}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def get_layer(self, scope_type: str, scope_id: str = "") -> dict:
        out: dict[str, dict] = {}
        for r in self._load()["rows"]:
            if r.get("scope_type") == scope_type and (r.get("scope_id") or "") == scope_id:
                out[r["agent_name"]] = {f: r.get(f) for f in _CONFIG_FIELDS}
        return out

    def set(self, scope_type: str, scope_id: str, agent_name: str,
            enabled=None, model=None, reasoning=None) -> None:
        scope_id = scope_id or ""
        with self._lock:
            data = self._load()
            for r in data["rows"]:
                if (r.get("scope_type") == scope_type and (r.get("scope_id") or "") == scope_id
                        and r.get("agent_name") == agent_name):
                    if enabled is not None:
                        r["enabled"] = enabled
                    if model is not None:
                        r["model"] = model
                    if reasoning is not None:
                        r["reasoning"] = reasoning
                    self._save(data)
                    return
            data["rows"].append({
                "scope_type": scope_type, "scope_id": scope_id, "agent_name": agent_name,
                "enabled": enabled, "model": model, "reasoning": reasoning,
            })
            self._save(data)

    def delete(self, scope_type: str, scope_id: str, agent_name: str) -> bool:
        scope_id = scope_id or ""
        with self._lock:
            data = self._load()
            before = len(data["rows"])
            data["rows"] = [
                r for r in data["rows"]
                if not (r.get("scope_type") == scope_type
                        and (r.get("scope_id") or "") == scope_id
                        and r.get("agent_name") == agent_name)
            ]
            if len(data["rows"]) != before:
                self._save(data)
                return True
        return False


def _select_backend():
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            from app.db.agent_config_repo import PostgresAgentConfigStore
            init_db()
            logger.info("Agent-config store backend: Postgres")
            return PostgresAgentConfigStore()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Postgres agent_config backend unavailable ({e}); falling back to JSON file")
    logger.info("Agent-config store backend: JSON file")
    return AgentConfigStore()


class _AgentConfigProxy:
    def __init__(self):
        self._impl = None

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def get_layer(self, *a, **k):
        return self._store().get_layer(*a, **k)

    def set(self, *a, **k):
        return self._store().set(*a, **k)

    def delete(self, *a, **k):
        return self._store().delete(*a, **k)


# module-wide singleton (backend chosen on first use)
agent_config = _AgentConfigProxy()
