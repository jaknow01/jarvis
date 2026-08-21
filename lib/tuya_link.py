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

import os
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
REAP_INTERVAL = 30.0     # upper bound on how often the idle reaper runs (s)
SCAN_TIMEOUT = 8         # bounded discovery scan for IP self-heal (s)
SCAN_CACHE_TTL = 10.0    # reuse one scan's id->ip result across a burst of failures (s)
DEFAULT_VERSION = 3.3

# How long a connection may sit unused before the reaper closes it (good-citizen
# release). Configurable in seconds via env; read at runtime so it picks up .env
# (loaded after import in app/main.py). See docs/TUYA_LOCAL.md.
IDLE_RELEASE_ENV = "TUYA_IDLE_RELEASE_SECONDS"
DEFAULT_IDLE_RELEASE = 120.0
_MIN_REAP_WAIT = 5.0     # floor so a tiny idle value can't spin the reaper


def get_idle_release() -> float:
    """Seconds a connection may stay idle before release, from
    ``TUYA_IDLE_RELEASE_SECONDS`` (falls back to the default on unset/invalid)."""
    raw = os.getenv(IDLE_RELEASE_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_IDLE_RELEASE
    try:
        val = float(raw)
    except ValueError:
        logger.warning(f"{IDLE_RELEASE_ENV}={raw!r} is not a number; using {DEFAULT_IDLE_RELEASE}s")
        return DEFAULT_IDLE_RELEASE
    if val <= 0:
        logger.warning(f"{IDLE_RELEASE_ENV}={val} must be > 0; using {DEFAULT_IDLE_RELEASE}s")
        return DEFAULT_IDLE_RELEASE
    return val


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
        # A LAN discovery scan (tinytuya.deviceScan) binds the Tuya broadcast UDP
        # ports (6666/6667/7000); two scans at once collide on those ports and the
        # broadcast that "wakes" a cold device is lost. So scans are serialized, and
        # the result is coalesced: a burst of failures shares ONE scan via a short
        # cache instead of each device launching its own.
        self._scan_lock = threading.Lock()      # only one deviceScan at a time
        self._scan_cache: dict[str, str] = {}   # dev_id -> ip, from the last scan
        self._scan_cache_ts: float = 0.0        # monotonic time of that scan

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

    def scan(self, timeout: int = SCAN_TIMEOUT, reason: str = "") -> dict[str, str]:
        """Run (or reuse) one LAN discovery scan; return the full ``{dev_id: ip}`` map.

        A broadcast scan also **wakes cold devices**: on first contact a bulb often
        refuses direct TCP (EHOSTUNREACH) until a scan has seen it (see
        docs/TUYA_LOCAL.md). Use this to prime/wake the fleet before probing, and as
        the IP self-heal source when a device's stored IP has drifted.

        Scans are **serialized and coalesced**: concurrent ``tinytuya.deviceScan``
        calls collide on the Tuya broadcast UDP ports and defeat the wake, so the
        first caller runs one scan and caches every id->ip it saw for
        ``SCAN_CACHE_TTL`` seconds; other callers within that window reuse it.
        """
        now = time.monotonic()
        # Fresh shared result from a scan another caller just ran? Reuse it.
        if now - self._scan_cache_ts < SCAN_CACHE_TTL and self._scan_cache:
            return self._scan_cache

        with self._scan_lock:
            # Re-check under the lock: a scan may have completed while we waited.
            now = time.monotonic()
            if now - self._scan_cache_ts < SCAN_CACHE_TTL and self._scan_cache:
                return self._scan_cache

            logger.info(f"Running LAN discovery scan{f' ({reason})' if reason else ''}")
            try:
                found = tinytuya.deviceScan(False, timeout)
            except Exception as e:  # noqa: BLE001
                logger.error(f"deviceScan failed: {e}")
                return {}

            fresh: dict[str, str] = {}
            for ip, info in found.items():
                found_id = info.get("gwId") or info.get("id")
                if found_id:
                    fresh[found_id] = ip
            self._scan_cache = fresh
            self._scan_cache_ts = time.monotonic()

        return self._scan_cache

    def rediscover_ip(self, dev_id: str, timeout: int = SCAN_TIMEOUT) -> Optional[str]:
        """Return a single device's current IP from a coalesced LAN scan, or None.

        Thin wrapper over :meth:`scan` used as a last-resort IP self-heal when
        retries keep failing (DHCP drift)."""
        return self.scan(timeout, reason=f"self-heal {dev_id}").get(dev_id)

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
        while True:
            idle_release = get_idle_release()
            # wake often enough to honour small idle values, but never busy-spin
            wait = max(_MIN_REAP_WAIT, min(REAP_INTERVAL, idle_release))
            if self._reaper_stop.wait(wait):
                return
            now = time.monotonic()
            expired = []
            with self._guard:
                for dev_id, conn in list(self._conns.items()):
                    # do not reap a connection mid-operation
                    if now - conn.last_used >= idle_release and conn.lock.acquire(blocking=False):
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
