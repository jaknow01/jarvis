"""
General long-term memory store for the ``memory_operator`` agent.

Structured natural-language entries (see ``docs/MEMORY.md``) persisted as JSON at
``data/memory_data/memory.json``. Small local file, atomic writes, guarded by a
lock. This module owns storage/CRUD; the agent-facing tools live in ``lib/tools.py``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_PATH = Path("data/memory_data/memory.json")
SUGGESTED_CATEGORIES = ["preferences", "facts", "habits", "interests", "routines"]
# Legacy JSON entries predate per-user scoping and have no user_id; they belong to the
# migrated owner (imported lazily to avoid any import-order coupling).
from lib.domain.principals import OWNER_USER_ID  # noqa: E402
VALID_SOURCES = ("user", "inferred")
VALID_CONFIDENCE = ("high", "medium", "low")
SUMMARY_CAP = 40


def _summary_cache_ttl() -> float:
    """TTL (seconds) for the coordinator's per-turn ``memory.summary()`` read, cached so it
    isn't a Postgres round-trip on every turn's hot path. ``MEMORY_SUMMARY_CACHE_TTL_SECONDS``;
    default 60. Set to 0 to disable caching (always read the store). Writes through this
    process invalidate the cache immediately, so a just-saved fact still shows next turn."""
    raw = os.getenv("MEMORY_SUMMARY_CACHE_TTL_SECONDS")
    if raw is None or raw.strip() == "":
        return 60.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def summarize(entries: list[dict], cap: int = SUMMARY_CAP) -> str:
    """Compact, grouped, human/LLM-readable digest of memory entries for prompt injection.

    Shared by both storage backends (JSON and Postgres)."""
    entries = entries[:cap]
    if not entries:
        return ""
    by_cat: dict[str, list[dict]] = {}
    for e in entries:
        by_cat.setdefault(e.get("category", "other"), []).append(e)
    lines = []
    for cat in sorted(by_cat):
        lines.append(f"[{cat}]")
        for e in by_cat[cat]:
            tag = "" if e.get("source") == "user" else " (inferred)"
            lines.append(f"  - {e.get('text', '')}{tag}")
    return "\n".join(lines)


class MemoryStore:
    def __init__(self, path: Path = MEMORY_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> dict:
        if not self.path.exists():
            return {"entries": []}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"memory: could not read {self.path}: {e}")
            return {"entries": []}
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            logger.error(f"memory: unexpected shape in {self.path}; ignoring")
            return {"entries": []}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)  # atomic swap

    # -- reads ----------------------------------------------------------------
    def _matches_user(self, e: dict, user_id: Optional[str]) -> bool:
        if user_id is None:
            return True  # unscoped read (legacy/global callers)
        eu = e.get("user_id")
        if eu is None:  # legacy entry with no owner → belongs to the migrated owner
            return user_id == OWNER_USER_ID
        return eu == user_id

    def all(self, user_id: Optional[str] = None) -> list[dict]:
        return [e for e in self._load()["entries"] if self._matches_user(e, user_id)]

    def by_category(self, category: str, user_id: Optional[str] = None) -> list[dict]:
        return [e for e in self.all(user_id) if e.get("category") == category]

    def summary(self, cap: int = SUMMARY_CAP, user_id: Optional[str] = None) -> str:
        return summarize(self.all(user_id), cap)

    # -- writes ---------------------------------------------------------------
    def add(self, text: str, category: str = "preferences",
            source: str = "user", confidence: str = "high",
            user_id: Optional[str] = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("memory text cannot be empty")
        category = (category or "preferences").strip() or "preferences"
        source = source if source in VALID_SOURCES else "user"
        confidence = confidence if confidence in VALID_CONFIDENCE else "high"
        now = _now()
        with self._lock:
            data = self._load()
            for e in data["entries"]:  # exact-text dedup within a (user, category)
                if (self._matches_user(e, user_id)
                        and e.get("category") == category
                        and e.get("text", "").strip().lower() == text.lower()):
                    e.update(updated_at=now, source=source, confidence=confidence)
                    self._save(data)
                    return e
            entry = {
                "id": "mem_" + uuid.uuid4().hex[:8],
                "text": text,
                "category": category,
                "source": source,
                "confidence": confidence,
                "user_id": user_id,
                "created_at": now,
                "updated_at": now,
            }
            data["entries"].append(entry)
            self._save(data)
            return entry

    def update(self, entry_id: str, text: Optional[str] = None,
               category: Optional[str] = None, confidence: Optional[str] = None,
               user_id: Optional[str] = None) -> Optional[dict]:
        with self._lock:
            data = self._load()
            for e in data["entries"]:
                if e["id"] == entry_id and self._matches_user(e, user_id):
                    if text is not None:
                        e["text"] = text.strip()
                    if category is not None:
                        e["category"] = category.strip()
                    if confidence in VALID_CONFIDENCE:
                        e["confidence"] = confidence
                    e["updated_at"] = _now()
                    self._save(data)
                    return e
        return None

    def delete(self, entry_id: str, user_id: Optional[str] = None) -> bool:
        with self._lock:
            data = self._load()
            remaining = [
                e for e in data["entries"]
                if not (e.get("id") == entry_id and self._matches_user(e, user_id))
            ]
            if len(remaining) != len(data["entries"]):
                data["entries"] = remaining
                self._save(data)
                return True
        return False


def _select_backend():
    """Choose the memory backend at first use (after .env is loaded): Postgres when
    DATABASE_URL is set, otherwise the on-disk JSON store."""
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            from app.db.memory_repo import PostgresMemoryStore
            init_db()
            logger.info("Memory store backend: Postgres")
            return PostgresMemoryStore()
    except Exception as e:  # noqa: BLE001 - never let storage selection crash the app
        logger.error(f"Postgres memory backend unavailable ({e}); falling back to JSON file")
    logger.info("Memory store backend: JSON file")
    return MemoryStore()


class _MemoryProxy:
    """Delegates to the backend chosen lazily on first use, so DATABASE_URL from
    .env (loaded after import) is respected. Keeps the ``memory`` singleton's API."""

    def __init__(self):
        self._impl = None
        # Short-TTL cache of summary() (the coordinator reads it every turn to build its
        # prompt). Keyed by (cap, user_id) -> (expiry_monotonic, text). Cleared on any write.
        self._summary_cache: dict[tuple, tuple[float, str]] = {}

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def _invalidate_summary(self) -> None:
        self._summary_cache.clear()

    def all(self, user_id: Optional[str] = None) -> list[dict]:
        return self._store().all(user_id)

    def by_category(self, category: str, user_id: Optional[str] = None) -> list[dict]:
        return self._store().by_category(category, user_id)

    def summary(self, cap: int = SUMMARY_CAP, user_id: Optional[str] = None) -> str:
        """Return the memory profile for ``user_id``, served from a short-TTL cache to keep
        it off the per-turn hot path (see ``_summary_cache_ttl``). Writes invalidate it."""
        ttl = _summary_cache_ttl()
        if ttl <= 0:
            return self._store().summary(cap, user_id)
        now = time.monotonic()
        key = (cap, user_id)
        hit = self._summary_cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
        val = self._store().summary(cap, user_id)
        self._summary_cache[key] = (now + ttl, val)
        return val

    def add(self, *args, **kwargs) -> dict:
        self._invalidate_summary()
        return self._store().add(*args, **kwargs)

    def update(self, *args, **kwargs):
        self._invalidate_summary()
        return self._store().update(*args, **kwargs)

    def delete(self, *args, **kwargs) -> bool:
        self._invalidate_summary()
        return self._store().delete(*args, **kwargs)


# module-wide singleton (backend chosen on first use)
memory = _MemoryProxy()
