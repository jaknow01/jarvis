"""Principals: users, channel identities (the whitelist), groups, and membership.

The identity table ``(channel, external_id)`` is the **whitelist**: an inbound pair
with no mapping does not resolve to a user and is ignored (no auto-provisioning, see
the ``user-identity`` capability). A user may belong to multiple groups; each group
carries a ``priority`` used to order groups during profile resolution.

Storage mirrors ``memory``/``scheduler``: an on-disk JSON store by default, swapped for
Postgres (``app/db/user_repo.py`` + ``app/db/group_repo.py``) when ``DATABASE_URL`` is
set. This module owns storage/CRUD; profile resolution lives in ``lib/domain/profile.py``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PRINCIPALS_PATH = Path("data/principals_data/principals.json")

# Stable ids for the migrated single-user setup: today's owner becomes this user in
# this default group, seeded from MESSENGER_ALLOWED_SENDER_ID (see docs/MULTI_USER.md).
OWNER_USER_ID = "owner"
DEFAULT_GROUP_ID = "grp_default"
DEFAULT_GROUP_NAME = "default"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_user_id() -> str:
    return "usr_" + uuid.uuid4().hex[:8]


def new_group_id() -> str:
    return "grp_" + uuid.uuid4().hex[:8]


class PrincipalStore:
    """On-disk JSON store for users/identities/groups/membership."""

    def __init__(self, path: Path = PRINCIPALS_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> dict:
        empty = {"users": [], "identities": [], "groups": [], "memberships": []}
        if not self.path.exists():
            return empty
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"principals: could not read {self.path}: {e}")
            return empty
        if not isinstance(data, dict):
            return empty
        for key in empty:
            data.setdefault(key, [])
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    # -- users / identities ---------------------------------------------------
    def resolve_user(self, channel: str, external_id: str) -> str | None:
        for i in self._load()["identities"]:
            if i.get("channel") == channel and i.get("external_id") == external_id:
                return i.get("user_id")
        return None

    def get_user(self, user_id: str) -> dict | None:
        return next((u for u in self._load()["users"] if u.get("id") == user_id), None)

    def list_users(self) -> list[dict]:
        return self._load()["users"]

    def identities_for(self, user_id: str) -> list[dict]:
        return [
            {"channel": i["channel"], "external_id": i["external_id"]}
            for i in self._load()["identities"] if i.get("user_id") == user_id
        ]

    def add_user(self, user_id: str, display_name: str | None, status: str = "active") -> dict:
        with self._lock:
            data = self._load()
            existing = next((u for u in data["users"] if u.get("id") == user_id), None)
            if existing:
                existing.update(display_name=display_name, status=status)
                self._save(data)
                return existing
            user = {"id": user_id, "display_name": display_name, "status": status,
                    "created_at": _now()}
            data["users"].append(user)
            self._save(data)
            return user

    def add_identity(self, user_id: str, channel: str, external_id: str) -> bool:
        with self._lock:
            data = self._load()
            for i in data["identities"]:
                if i.get("channel") == channel and i.get("external_id") == external_id:
                    return False  # (channel, external_id) already mapped
            data["identities"].append(
                {"user_id": user_id, "channel": channel, "external_id": external_id}
            )
            self._save(data)
            return True

    # -- groups / membership --------------------------------------------------
    def get_group(self, group_id: str) -> dict | None:
        return next((g for g in self._load()["groups"] if g.get("id") == group_id), None)

    def get_group_by_name(self, name: str) -> dict | None:
        return next((g for g in self._load()["groups"] if g.get("name") == name), None)

    def list_groups(self) -> list[dict]:
        return sorted(self._load()["groups"], key=lambda g: (g.get("priority", 0), g.get("name", "")))

    def groups_for_user(self, user_id: str) -> list[dict]:
        data = self._load()
        member_ids = {m["group_id"] for m in data["memberships"] if m.get("user_id") == user_id}
        groups = [g for g in data["groups"] if g.get("id") in member_ids]
        return sorted(groups, key=lambda g: (g.get("priority", 0), g.get("name", "")))

    def members_of(self, group_id: str) -> list[str]:
        return [m["user_id"] for m in self._load()["memberships"] if m.get("group_id") == group_id]

    def add_group(self, group_id: str, name: str, priority: int = 0) -> dict:
        with self._lock:
            data = self._load()
            existing = next((g for g in data["groups"] if g.get("name") == name), None)
            if existing:
                existing["priority"] = int(priority)
                self._save(data)
                return existing
            group = {"id": group_id, "name": name, "priority": int(priority), "created_at": _now()}
            data["groups"].append(group)
            self._save(data)
            return group

    def set_priority(self, group_id: str, priority: int) -> bool:
        with self._lock:
            data = self._load()
            for g in data["groups"]:
                if g.get("id") == group_id:
                    g["priority"] = int(priority)
                    self._save(data)
                    return True
        return False

    def add_membership(self, user_id: str, group_id: str) -> bool:
        with self._lock:
            data = self._load()
            for m in data["memberships"]:
                if m.get("user_id") == user_id and m.get("group_id") == group_id:
                    return False
            data["memberships"].append({"user_id": user_id, "group_id": group_id})
            self._save(data)
            return True

    def remove_membership(self, user_id: str, group_id: str) -> bool:
        with self._lock:
            data = self._load()
            before = len(data["memberships"])
            data["memberships"] = [
                m for m in data["memberships"]
                if not (m.get("user_id") == user_id and m.get("group_id") == group_id)
            ]
            if len(data["memberships"]) != before:
                self._save(data)
                return True
        return False


class _PostgresPrincipalStore:
    """Facade over the two Postgres repos so the proxy has one backend object."""

    def __init__(self):
        from app.db.user_repo import PostgresUserStore
        from app.db.group_repo import PostgresGroupStore
        self._users = PostgresUserStore()
        self._groups = PostgresGroupStore()

    # users / identities
    def resolve_user(self, channel, external_id): return self._users.resolve_user(channel, external_id)
    def get_user(self, user_id): return self._users.get_user(user_id)
    def list_users(self): return self._users.list_users()
    def identities_for(self, user_id): return self._users.identities_for(user_id)
    def add_user(self, user_id, display_name, status="active"):
        return self._users.add_user(user_id, display_name, status)
    def add_identity(self, user_id, channel, external_id):
        return self._users.add_identity(user_id, channel, external_id)

    # groups / membership
    def get_group(self, group_id): return self._groups.get_group(group_id)
    def get_group_by_name(self, name): return self._groups.get_group_by_name(name)
    def list_groups(self): return self._groups.list_groups()
    def groups_for_user(self, user_id): return self._groups.groups_for_user(user_id)
    def members_of(self, group_id): return self._groups.members_of(group_id)
    def add_group(self, group_id, name, priority=0): return self._groups.add_group(group_id, name, priority)
    def set_priority(self, group_id, priority): return self._groups.set_priority(group_id, priority)
    def add_membership(self, user_id, group_id): return self._groups.add_membership(user_id, group_id)
    def remove_membership(self, user_id, group_id): return self._groups.remove_membership(user_id, group_id)


def _select_backend():
    try:
        from app.db import connection as dbconn
        if dbconn.is_configured():
            from app.db.schema import init_db
            init_db()
            logger.info("Principals store backend: Postgres")
            return _PostgresPrincipalStore()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Postgres principals backend unavailable ({e}); falling back to JSON file")
    logger.info("Principals store backend: JSON file")
    return PrincipalStore()


class _PrincipalProxy:
    """Delegates to the backend chosen lazily on first use (so .env is respected)."""

    def __init__(self):
        self._impl = None

    def _store(self):
        if self._impl is None:
            self._impl = _select_backend()
        return self._impl

    def __getattr__(self, name):
        # Delegate every store method to the lazily-selected backend.
        return getattr(self._store(), name)


# module-wide singleton (backend chosen on first use)
principals = _PrincipalProxy()


def seed_owner(display_name: str = "Owner") -> str:
    """Idempotently ensure the migrated single-user owner exists: a ``default`` group,
    an ``owner`` user, their membership, and (when ``MESSENGER_ALLOWED_SENDER_ID`` is
    set) the owner's Messenger identity. Safe to call on every startup and on both
    backends — the underlying add_* methods upsert/dedupe. Returns the owner user id.
    """
    principals.add_group(DEFAULT_GROUP_ID, DEFAULT_GROUP_NAME, 0)
    principals.add_user(OWNER_USER_ID, display_name)
    principals.add_membership(OWNER_USER_ID, DEFAULT_GROUP_ID)
    allowed = (os.getenv("MESSENGER_ALLOWED_SENDER_ID") or "").strip()
    if allowed and principals.resolve_user("messenger", allowed) is None:
        principals.add_identity(OWNER_USER_ID, "messenger", allowed)
    return OWNER_USER_ID
