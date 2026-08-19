"""
General long-term memory store for the ``memory_operator`` agent.

Structured natural-language entries (see ``docs/MEMORY.md``) persisted as JSON at
``data/memory_data/memory.json``. Small local file, atomic writes, guarded by a
lock. This module owns storage/CRUD; the agent-facing tools live in ``lib/tools.py``.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_PATH = Path("data/memory_data/memory.json")
SUGGESTED_CATEGORIES = ["preferences", "facts", "habits", "interests", "routines"]
VALID_SOURCES = ("user", "inferred")
VALID_CONFIDENCE = ("high", "medium", "low")
SUMMARY_CAP = 40


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
    def all(self) -> list[dict]:
        return self._load()["entries"]

    def by_category(self, category: str) -> list[dict]:
        return [e for e in self.all() if e.get("category") == category]

    def summary(self, cap: int = SUMMARY_CAP) -> str:
        """Compact, grouped, human/LLM-readable digest for prompt injection."""
        entries = self.all()[:cap]
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

    # -- writes ---------------------------------------------------------------
    def add(self, text: str, category: str = "preferences",
            source: str = "user", confidence: str = "high") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("memory text cannot be empty")
        category = (category or "preferences").strip() or "preferences"
        source = source if source in VALID_SOURCES else "user"
        confidence = confidence if confidence in VALID_CONFIDENCE else "high"
        now = _now()
        with self._lock:
            data = self._load()
            for e in data["entries"]:  # exact-text dedup within a category
                if e.get("category") == category and e.get("text", "").strip().lower() == text.lower():
                    e.update(updated_at=now, source=source, confidence=confidence)
                    self._save(data)
                    return e
            entry = {
                "id": "mem_" + uuid.uuid4().hex[:8],
                "text": text,
                "category": category,
                "source": source,
                "confidence": confidence,
                "created_at": now,
                "updated_at": now,
            }
            data["entries"].append(entry)
            self._save(data)
            return entry

    def update(self, entry_id: str, text: Optional[str] = None,
               category: Optional[str] = None, confidence: Optional[str] = None) -> Optional[dict]:
        with self._lock:
            data = self._load()
            for e in data["entries"]:
                if e["id"] == entry_id:
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

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            data = self._load()
            remaining = [e for e in data["entries"] if e.get("id") != entry_id]
            if len(remaining) != len(data["entries"]):
                data["entries"] = remaining
                self._save(data)
                return True
        return False


# module-wide singleton
memory = MemoryStore()
