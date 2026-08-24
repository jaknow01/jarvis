"""Conversation-history store for the assistant.

Owns per-channel conversations and their user/assistant messages. Since the
migration to LlamaIndex dropped the OpenAI Responses API continuity
(``previous_response_id``), we are the source of truth for continuity: each turn
loads the full prior history and replays it as ``chat_history`` to the coordinator.

Only two messages are stored per turn — the user's message and the composer's
final reply — never the internal coordinator/sub-agent/tool exchanges. The
assistant message carries the run's MLflow trace id (empty when tracing is off).

This module owns the on-disk JSON backend and picks Postgres
(``app/db/conversation_repo.py``) when ``DATABASE_URL`` is set, mirroring the
memory and scheduler stores (see ``docs/STORAGE.md``).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CONVERSATIONS_PATH = Path("data/conversation_data/conversations.json")
VALID_ROLES = ("user", "assistant")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ConversationStore:
    """On-disk JSON conversation store, keyed by ``channel_key``."""

    def __init__(self, path: Path = CONVERSATIONS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> dict:
        if not self.path.exists():
            return {"conversations": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"conversation: could not read {self.path}: {e}")
            return {"conversations": {}}
        if not isinstance(data, dict) or not isinstance(data.get("conversations"), dict):
            logger.error(f"conversation: unexpected shape in {self.path}; ignoring")
            return {"conversations": {}}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)  # atomic swap

    def _conversation(self, data: dict, channel_key: str) -> dict:
        conv = data["conversations"].get(channel_key)
        if conv is None:
            conv = {
                "id": str(uuid.uuid4()),
                "channel_key": channel_key,
                "created_at": _now(),
                "last_active_at": _now(),
                "messages": [],
            }
            data["conversations"][channel_key] = conv
        return conv

    # -- reads ----------------------------------------------------------------
    def load_history(self, channel_key: str) -> list[dict]:
        """Return this channel's messages as ordered ``{"role", "content"}`` dicts."""
        conv = self._load()["conversations"].get(channel_key)
        if not conv:
            return []
        msgs = sorted(conv.get("messages", []), key=lambda m: m.get("turn_index", 0))
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    # -- writes ---------------------------------------------------------------
    def record_turn(self, channel_key: str, user_text: str, assistant_text: str,
                    trace_id: str = "") -> None:
        """Append the user message and the assistant reply for one completed turn."""
        with self._lock:
            data = self._load()
            conv = self._conversation(data, channel_key)
            msgs = conv["messages"]
            base = len(msgs)
            now = _now()
            msgs.append({
                "id": str(uuid.uuid4()), "role": "user", "content": user_text or "",
                "turn_index": base, "created_at": now, "mlflow_trace_id": "",
            })
            msgs.append({
                "id": str(uuid.uuid4()), "role": "assistant", "content": assistant_text or "",
                "turn_index": base + 1, "created_at": now, "mlflow_trace_id": trace_id or "",
            })
            conv["last_active_at"] = now
            self._save(data)


def _select_backend():
    """Choose the conversation backend at first use (after .env is loaded): Postgres
    when DATABASE_URL is set, otherwise the on-disk JSON store."""
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            from app.db.conversation_repo import PostgresConversationStore
            init_db()
            logger.info("Conversation store backend: Postgres")
            return PostgresConversationStore()
    except Exception as e:  # noqa: BLE001 - never let storage selection crash the app
        logger.error(f"Postgres conversation backend unavailable ({e}); falling back to JSON file")
    logger.info("Conversation store backend: JSON file")
    return ConversationStore()


class _ConversationProxy:
    """Delegates to the backend chosen lazily on first use, so DATABASE_URL from
    .env (loaded after import) is respected. Keeps the ``conversations`` singleton's API."""

    def __init__(self):
        self._impl = None

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def load_history(self, *args, **kwargs) -> list[dict]:
        return self._store().load_history(*args, **kwargs)

    def record_turn(self, *args, **kwargs) -> None:
        return self._store().record_turn(*args, **kwargs)


# module-wide singleton (backend chosen on first use)
conversations = _ConversationProxy()
