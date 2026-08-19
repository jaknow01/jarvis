"""
Process-wide connection manager for the Tuya local protocol.

Rationale and decisions are documented in ``docs/TUYA_LOCAL.md``. In short: reuse
one persistent connection per device (removes the cold TCP handshake from every
command), but be a "good citizen" — release connections after an idle period rather
than holding them warm 24/7. All device I/O elsewhere is hard-bounded so a wedged
device can never hang the app.

This module owns only the *connection lifecycle* (open / cache / per-device lock /
idle-release / IP self-heal). The bounded-retry policy and the actual bulb
operations live in ``lib/smart_device.py``.
"""
from __future__ import annotations

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import tinytuya

logger = logging.getLogger(__name__)

# --- tunables (see docs/TUYA_LOCAL.md) ---
SOCKET_TIMEOUT = 3.0     # per-call tinytuya socket timeout (s)
RETRY_LIMIT = 1          # tinytuya's own internal retry count
HARD_TIMEOUT = 8.0       # absolute cap per call, enforced by callers via wait_for (s)
MAX_ATTEMPTS = 3         # bounded reconnect-and-retry per logical operation
IDLE_RELEASE = 120.0     # close a connection unused for this long (s)
REAP_INTERVAL = 30.0     # how often the idle reaper runs (s)
SCAN_TIMEOUT = 8         # bounded discovery scan for IP self-heal (s)
DEFAULT_VERSION = 3.3


@dataclass
class _Conn:
    bulb: tinytuya.BulbDevice
    ip: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)


class TuyaConnectionManager:
    """Caches one persistent :class:`tinytuya.BulbDevice` per device id."""

    def __init__(self):
        self._conns: dict[str, _Conn] = {}
        self._guard = threading.Lock()      # protects the _conns dict
        self._reaper: Optional[threading.Thread] = None
        self._reaper_stop = threading.Event()

    # -- connection lifecycle -------------------------------------------------
    def get(self, dev_id: str, ip: str, local_key: str, version: float = DEFAULT_VERSION) -> _Conn:
        """Return a cached persistent connection, opening one if needed.

        If the cached connection was opened against a different IP (e.g. after an
        IP self-heal), it is rebuilt.
        """
        self._ensure_reaper()
        with self._guard:
            conn = self._conns.get(dev_id)
            if conn is not None and conn.ip == ip:
                conn.last_used = time.monotonic()
                return conn
            # stale (IP changed) or missing -> (re)build
            if conn is not None:
                self._close(conn)
            bulb = self._build_bulb(dev_id, ip, local_key, version)
            conn = _Conn(bulb=bulb, ip=ip)
            self._conns[dev_id] = conn
            logger.info(f"Opened persistent Tuya connection to {dev_id} at {ip}")
            return conn

    def _build_bulb(self, dev_id: str, ip: str, local_key: str, version: float) -> tinytuya.BulbDevice:
        bulb = tinytuya.BulbDevice(dev_id=dev_id, address=ip, local_key=local_key, version=version)
        bulb.set_socketTimeout(SOCKET_TIMEOUT)
        bulb.set_socketRetryLimit(RETRY_LIMIT)
        bulb.set_socketPersistent(True)
        return bulb

    def invalidate(self, dev_id: str) -> None:
        """Drop and close a device's connection (e.g. after a failure/timeout),
        so the next call reopens a fresh, warm connection."""
        with self._guard:
            conn = self._conns.pop(dev_id, None)
        if conn is not None:
            self._close(conn)
            logger.info(f"Invalidated Tuya connection to {dev_id}")

    def rediscover_ip(self, dev_id: str, timeout: int = SCAN_TIMEOUT) -> Optional[str]:
        """Broadcast-scan the LAN and return the device's current IP, or None.

        Used as a last-resort IP self-heal when retries keep failing (DHCP drift).
        """
        logger.info(f"Rediscovering IP for {dev_id} via LAN scan")
        try:
            found = tinytuya.deviceScan(False, timeout)
        except Exception as e:  # noqa: BLE001
            logger.error(f"deviceScan failed: {e}")
            return None
        for ip, info in found.items():
            if (info.get("gwId") or info.get("id")) == dev_id:
                return ip
        return None

    # -- idle reaper (good citizen) ------------------------------------------
    def _ensure_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return
        with self._guard:
            if self._reaper is not None and self._reaper.is_alive():
                return
            self._reaper_stop.clear()
            self._reaper = threading.Thread(target=self._reap_loop, name="tuya-reaper", daemon=True)
            self._reaper.start()

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(REAP_INTERVAL):
            now = time.monotonic()
            expired = []
            with self._guard:
                for dev_id, conn in list(self._conns.items()):
                    # do not reap a connection mid-operation
                    if now - conn.last_used >= IDLE_RELEASE and conn.lock.acquire(blocking=False):
                        try:
                            expired.append(self._conns.pop(dev_id))
                        finally:
                            conn.lock.release()
            for conn in expired:
                self._close(conn)
                logger.info(f"Released idle Tuya connection at {conn.ip}")

    # -- teardown -------------------------------------------------------------
    @staticmethod
    def _close(conn: _Conn) -> None:
        try:
            conn.bulb.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            pass

    def close_all(self) -> None:
        self._reaper_stop.set()
        with self._guard:
            conns = list(self._conns.values())
            self._conns.clear()
        for conn in conns:
            self._close(conn)


# module-wide singleton
manager = TuyaConnectionManager()
