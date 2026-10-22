"""API keys for the public MCP tool servers (see docs/MCP.md).

Clients live in ``config/mcp_clients.yaml`` (gitignored; path overridable with
``MCP_CLIENTS_CONFIG``). The file stores only the SHA-256 of each key, never the key
itself, so a leaked config does not leak access. Each client says which servers it may
use and carries per-server defaults (e.g. its own FPL manager/league id)::

    clients:
      marek:
        name: Marek
        key_sha256: "3f1c…"          # from: python -m scripts.mcp_key new marek
        servers: [fpl]
        rate_limit_per_minute: 30    # optional, else MCP_RATE_LIMIT_PER_MINUTE
        fpl:
          entry_id: 1234567
          league_id: 765432

The file is re-read when it changes (mtime), so adding or revoking a client needs no
restart. Keys look like ``jvk_<random>``; generate them with ``scripts/mcp_key.py``.
"""

import hashlib
import logging
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from lib.core import env_int

logger = logging.getLogger(__name__)

KEY_PREFIX = "jvk_"
DEFAULT_CONFIG_PATH = Path("config/mcp_clients.yaml")
_ALLOWED_CLIENT_KEYS = {"name", "key_sha256", "servers", "enabled", "rate_limit_per_minute"}


class McpClientsConfigError(ValueError):
    """The clients file is malformed."""


@dataclass(frozen=True)
class McpClient:
    """One API-key holder."""

    client_id: str
    name: str
    key_sha256: str
    servers: frozenset[str]
    rate_limit_per_minute: Optional[int] = None
    # Per-server defaults, e.g. settings["fpl"] == {"entry_id": 1, "league_id": 2}.
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = "config"  # "config" (YAML file) or "join" (self-service page)

    def allows(self, server: str) -> bool:
        return server in self.servers

    def server_settings(self, server: str) -> dict[str, Any]:
        return dict(self.settings.get(server) or {})


def new_key() -> str:
    """A fresh random API key (shown once to its holder, stored only as a hash)."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def config_path() -> Path:
    return Path(os.getenv("MCP_CLIENTS_CONFIG") or DEFAULT_CONFIG_PATH)


def parse_clients(raw: Any, known_servers: Optional[set[str]] = None) -> dict[str, McpClient]:
    """Validate the YAML document and index the enabled clients by key hash."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("clients", {}), dict):
        raise McpClientsConfigError("expected a top-level 'clients:' mapping")
    by_hash: dict[str, McpClient] = {}
    for client_id, entry in (raw.get("clients") or {}).items():
        if not isinstance(entry, dict):
            raise McpClientsConfigError(f"client {client_id!r}: expected a mapping")
        digest = str(entry.get("key_sha256", "")).strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise McpClientsConfigError(f"client {client_id!r}: key_sha256 must be a 64-char hex SHA-256")
        servers = entry.get("servers") or []
        if not isinstance(servers, list) or not all(isinstance(s, str) for s in servers):
            raise McpClientsConfigError(f"client {client_id!r}: servers must be a list of names")
        settings = {k: v for k, v in entry.items() if k not in _ALLOWED_CLIENT_KEYS}
        for server, value in settings.items():
            if known_servers is not None and server not in known_servers:
                raise McpClientsConfigError(f"client {client_id!r}: unknown key {server!r}")
            if not isinstance(value, dict):
                raise McpClientsConfigError(f"client {client_id!r}: {server!r} must be a mapping")
        rate = entry.get("rate_limit_per_minute")
        if rate is not None and (not isinstance(rate, int) or rate < 1):
            raise McpClientsConfigError(f"client {client_id!r}: rate_limit_per_minute must be a positive int")
        if entry.get("enabled", True) is False:
            continue
        if digest in by_hash:
            raise McpClientsConfigError(f"client {client_id!r}: duplicate key (also {by_hash[digest].client_id!r})")
        by_hash[digest] = McpClient(
            client_id=str(client_id),
            name=str(entry.get("name") or client_id),
            key_sha256=digest,
            servers=frozenset(servers),
            rate_limit_per_minute=rate,
            settings=settings,
        )
    return by_hash


def client_from_issued(row: dict) -> McpClient:
    """A self-service key (``lib.domain.mcp_keys`` row) as a client: its FPL defaults are
    the entry and league it was issued for."""
    entry = int(row["entry_id"])
    name = row.get("manager_name") or f"FPL {entry}"
    if row.get("team_name"):
        name = f"{name} ({row['team_name']})"
    return McpClient(
        client_id=f"fpl-{entry}",
        name=name,
        key_sha256=str(row["key_sha256"]).lower(),
        servers=frozenset(row.get("servers") or ["fpl"]),
        settings={"fpl": {"entry_id": entry, "league_id": row.get("league_id")}},
        source="join",
    )


class McpClientRegistry:
    """Looks up clients by API key, from two sources:

    * the YAML file (hand-managed; re-read when it changes) — a broken file keeps the last
      good version in force rather than locking everyone out or letting everyone in;
    * optionally a store of self-service keys (``store.active()`` rows), cached for
      ``MCP_KEYS_REFRESH_SECONDS`` (default 15) — so a revocation made from another
      process (the admin ``/mcp revoke``) takes effect within that window.
    """

    def __init__(self, path: Optional[Path] = None, known_servers: Optional[set[str]] = None,
                 store: Any = None):
        self._path = path
        self._known = known_servers
        self._mtime: Optional[float] = None
        self._by_hash: dict[str, McpClient] = {}
        self._lock = threading.Lock()
        self._store = store
        self._issued: dict[str, McpClient] = {}
        self._issued_at: Optional[float] = None

    def invalidate(self) -> None:
        """Force the next lookup to re-read the self-service store (after an issue/revoke)."""
        self._issued_at = None

    def _reload_issued(self) -> None:
        if self._store is None:
            return
        now = time.monotonic()
        ttl = env_int("MCP_KEYS_REFRESH_SECONDS", 15, minimum=0)
        if self._issued_at is not None and now - self._issued_at < ttl:
            return
        self._issued_at = now
        try:
            rows = self._store.active()
        except Exception as e:  # DB down: keep the previous set rather than fail open/closed
            logger.error("MCP key store unavailable, keeping the previous key set: %s", e)
            return
        self._issued = {c.key_sha256: c for c in map(client_from_issued, rows)}

    @property
    def path(self) -> Path:
        return self._path or config_path()

    def _reload_if_changed(self) -> None:
        path = self.path
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            if self._mtime is not None or self._by_hash:
                logger.warning("MCP clients file %s disappeared — no client can authenticate", path)
            self._mtime, self._by_hash = None, {}
            return
        if mtime == self._mtime:
            return
        with self._lock:
            if mtime == self._mtime:
                return
            try:
                by_hash = parse_clients(yaml.safe_load(path.read_text()), self._known)
            except (McpClientsConfigError, yaml.YAMLError) as e:
                logger.error("MCP clients file %s is invalid, keeping the previous version: %s", path, e)
                self._mtime = mtime  # don't re-parse the same broken file every request
                return
            self._by_hash, self._mtime = by_hash, mtime
            logger.info("Loaded %d MCP client(s) from %s", len(by_hash), path)

    def authenticate(self, key: Optional[str]) -> Optional[McpClient]:
        """The client holding ``key``, or None (unknown, disabled or malformed key)."""
        if not key or not key.startswith(KEY_PREFIX) or len(key) > 256:
            return None
        self._reload_if_changed()
        digest = hash_key(key)
        if digest in self._by_hash:
            return self._by_hash[digest]
        self._reload_issued()
        return self._issued.get(digest)

    def clients(self) -> list[McpClient]:
        self._reload_if_changed()
        self._reload_issued()
        return sorted([*self._by_hash.values(), *self._issued.values()], key=lambda c: c.client_id)


class RateLimiter:
    """Sliding one-minute window of tool calls per client (in memory, per process)."""

    def __init__(self, default_per_minute: Optional[int] = None):
        self._default = default_per_minute
        self._calls: dict[str, deque] = {}
        self._lock = threading.Lock()

    def default_limit(self) -> int:
        if self._default is not None:
            return self._default
        return env_int("MCP_RATE_LIMIT_PER_MINUTE", 60, minimum=1)

    def allow(self, client: McpClient, now: Optional[float] = None) -> bool:
        limit = client.rate_limit_per_minute or self.default_limit()
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._calls.setdefault(client.client_id, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                return False
            window.append(now)
            return True
