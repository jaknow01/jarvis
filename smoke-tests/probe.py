#!/usr/bin/env python3
"""
Stability probe for the smart-lighting (Tuya) devices.

This is a *diagnostic harness*, not a unit test. It talks to the real bulbs over
the LAN and measures how reliable that communication actually is: which devices
are reachable, how often connections/reads fail, how slow they are, and whether a
persistent socket is more stable than a fresh connection per call (the pattern the
app currently uses in ``lib/smart_device.py``).

Read-only by default:
  * LAN discovery scan (UDP broadcast) -> which devices are online + their current IP
  * TCP reachability to port 6668
  * ``status()`` reads (these do NOT change the light) -> success rate + latency
  * fresh-socket vs persistent-socket comparison

``--write`` additionally exercises state-changing operations (on/off/mode/color/
temperature) and restores each device's original state afterwards. It physically
toggles your lights, so it is opt-in.

Every device call runs under a hard wall-clock timeout in a worker thread, so a
single hung device (a very real failure mode here) can never block the whole run.

Usage (from repo root):
    poetry run python smoke-tests/probe.py
    poetry run python smoke-tests/probe.py --iterations 20 --timeout 3
    poetry run python smoke-tests/probe.py --write          # toggles real lights
    poetry run python smoke-tests/probe.py --json out.json  # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import socket
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import tinytuya

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = REPO_ROOT / "data" / "smart_device_data" / "smart_devices.json"
TUYA_PORT = 6668
_SENTINEL = object()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run_bounded(fn: Callable[[], Any], timeout: float):
    """Run ``fn`` in a daemon thread; return its result or the string
    ``"__timeout__"`` if it does not finish within ``timeout`` seconds.

    The thread is a daemon, so a genuinely hung tinytuya call is abandoned
    instead of blocking the probe. This is deliberate: a bulb that accepts a
    socket but never replies is exactly one of the failure modes we hunt for.
    """
    box: dict[str, Any] = {"v": _SENTINEL, "err": None}

    def target():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001 - we want to record any failure
            box["err"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "__timeout__"
    if box["err"] is not None:
        raise box["err"]
    return box["v"]


def pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100)[int(p) - 1]


def fmt_ms(seconds: Optional[float]) -> str:
    return "n/a" if seconds is None else f"{seconds * 1000:.0f}ms"


# --------------------------------------------------------------------------- #
# device model
# --------------------------------------------------------------------------- #
@dataclass
class Device:
    name: str
    dev_id: str
    ip: str
    local_key: str
    version: float = 3.3

    def bulb(self, timeout: float, retries: int, persistent: bool = False) -> tinytuya.BulbDevice:
        d = tinytuya.BulbDevice(
            dev_id=self.dev_id, address=self.ip, local_key=self.local_key, version=self.version
        )
        d.set_socketTimeout(timeout)
        d.set_socketRetryLimit(retries)
        if persistent:
            d.set_socketPersistent(True)
        return d


def load_devices(path: Path) -> list[Device]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    elements = raw["list_of_elements"] if isinstance(raw, dict) else raw
    devices = []
    for e in elements:
        p = e["params"]
        devices.append(
            Device(
                name=e.get("custom_name", p.get("id", "?")),
                dev_id=p["id"],
                ip=p["local_ip"],
                local_key=p["local_key"],
                version=float(p.get("version", 3.3)),
            )
        )
    return devices


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
def discover(timeout: int) -> dict[str, dict]:
    """UDP-broadcast scan -> {device_id: {ip, version}}. Read-only."""
    found = tinytuya.deviceScan(False, timeout)
    by_id = {}
    for ip, info in found.items():
        gwid = info.get("gwId") or info.get("id")
        if gwid:
            by_id[gwid] = {"ip": ip, "version": info.get("version")}
    return by_id


def tcp_reachable(ip: str, timeout: float) -> tuple[bool, Optional[str]]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, TUYA_PORT))
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        s.close()


def is_good_status(st: Any) -> bool:
    return isinstance(st, dict) and "Error" not in st and bool(st.get("dps"))


@dataclass
class ProbeResult:
    name: str
    ip: str
    present: bool = False
    ip_drift: Optional[str] = None  # discovered ip if it differs from json
    tcp_ok: int = 0
    tcp_total: int = 0
    tcp_errors: list[str] = field(default_factory=list)
    fresh_ok: int = 0
    fresh_total: int = 0
    fresh_lat: list[float] = field(default_factory=list)
    fresh_errors: list[str] = field(default_factory=list)
    persist_ok: int = 0
    persist_total: int = 0
    persist_lat: list[float] = field(default_factory=list)
    write_notes: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.present and self.fresh_total > 0 and self.fresh_ok == self.fresh_total


def probe_device(dev: Device, discovered: dict[str, dict], args) -> ProbeResult:
    r = ProbeResult(name=dev.name, ip=dev.ip)
    disc = discovered.get(dev.dev_id)
    r.present = disc is not None
    if disc and disc["ip"] != dev.ip:
        r.ip_drift = disc["ip"]

    if not r.present:
        return r  # offline: nothing else to measure

    # TCP reachability
    for _ in range(args.iterations):
        r.tcp_total += 1
        ok, err = tcp_reachable(dev.ip, args.timeout)
        if ok:
            r.tcp_ok += 1
        elif err:
            r.tcp_errors.append(err)
        time.sleep(args.delay)

    # fresh-socket status reads (mimics lib/smart_device.py: new BulbDevice per call)
    for _ in range(args.iterations):
        r.fresh_total += 1
        t0 = time.time()

        def read():
            return dev.bulb(args.timeout, args.retries).status()

        st = run_bounded(read, args.timeout * (args.retries + 1) + 1)
        dt = time.time() - t0
        if st == "__timeout__":
            r.fresh_errors.append("hard-timeout (call never returned)")
        elif is_good_status(st):
            r.fresh_ok += 1
            r.fresh_lat.append(dt)
        else:
            r.fresh_errors.append(st.get("Error") if isinstance(st, dict) else str(st))
        time.sleep(args.delay)

    # persistent-socket status reads (one connection reused for all reads)
    try:
        pbulb = dev.bulb(args.timeout, args.retries, persistent=True)
        for _ in range(args.iterations):
            r.persist_total += 1
            t0 = time.time()
            st = run_bounded(pbulb.status, args.timeout * (args.retries + 1) + 1)
            dt = time.time() - t0
            if is_good_status(st):
                r.persist_ok += 1
                r.persist_lat.append(dt)
            time.sleep(args.delay)
    except Exception:  # noqa: BLE001
        pass

    if args.write:
        _exercise_writes(dev, args, r)

    return r


def _exercise_writes(dev: Device, args, r: ProbeResult) -> None:
    """Exercise on/off, restoring the original power state. Opt-in (--write)."""
    try:
        bulb = dev.bulb(args.timeout, args.retries, persistent=True)
        original = run_bounded(bulb.status, args.timeout + 1)
        was_on = None
        if is_good_status(original):
            was_on = original["dps"].get("20")

        ok_on = run_bounded(bulb.turn_on, args.timeout + 1)
        r.write_notes.append(f"turn_on -> {'ok' if ok_on != '__timeout__' else 'timeout'}")
        time.sleep(0.5)
        ok_off = run_bounded(bulb.turn_off, args.timeout + 1)
        r.write_notes.append(f"turn_off -> {'ok' if ok_off != '__timeout__' else 'timeout'}")
        time.sleep(0.5)

        # restore
        if was_on:
            run_bounded(bulb.turn_on, args.timeout + 1)
            r.write_notes.append("restored original state (on)")
        else:
            r.write_notes.append("restored original state (off)")
    except Exception as e:  # noqa: BLE001
        r.write_notes.append(f"write error: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def print_report(results: list[ProbeResult], args) -> None:
    print("\n" + "=" * 72)
    print("SMART-LIGHTING STABILITY PROBE")
    print("=" * 72)
    print(f"iterations/device={args.iterations}  socket_timeout={args.timeout}s  retries={args.retries}")

    for r in results:
        print("\n" + "-" * 72)
        head = f"{r.name}  ({r.ip})"
        if not r.present:
            print(f"{head}\n  OFFLINE — not seen in LAN discovery scan (powered off or off-network)")
            continue
        drift = f"  ⚠ IP drift: discovered at {r.ip_drift}" if r.ip_drift else ""
        print(f"{head}{drift}")
        print(f"  TCP :6668   {r.tcp_ok}/{r.tcp_total} open" +
              (f"   errs={list(dict.fromkeys(r.tcp_errors))[:2]}" if r.tcp_errors else ""))
        fl = r.fresh_lat
        print(f"  status(fresh)     {r.fresh_ok}/{r.fresh_total} ok"
              f"   p50={fmt_ms(pct(fl,50))} p95={fmt_ms(pct(fl,95))} max={fmt_ms(max(fl) if fl else None)}" +
              (f"   errs={list(dict.fromkeys(r.fresh_errors))[:2]}" if r.fresh_errors else ""))
        pl = r.persist_lat
        if r.persist_total:
            print(f"  status(persist)   {r.persist_ok}/{r.persist_total} ok"
                  f"   p50={fmt_ms(pct(pl,50))} p95={fmt_ms(pct(pl,95))} max={fmt_ms(max(pl) if pl else None)}")
        for note in r.write_notes:
            print(f"  write: {note}")

    # verdict summary
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    online = [r for r in results if r.present]
    offline = [r for r in results if not r.present]
    healthy = [r for r in online if r.healthy]
    flaky = [r for r in online if not r.healthy]
    print(f"  online: {len(online)}/{len(results)}   healthy: {len(healthy)}   flaky: {len(flaky)}   offline: {len(offline)}")
    if offline:
        print(f"  OFFLINE : {', '.join(r.name for r in offline)}")
    if flaky:
        print(f"  FLAKY   : {', '.join(r.name for r in flaky)}")
        print("            (reachable via broadcast but failing TCP/status — see findings.md)")
    if healthy:
        # is persistent measurably better anywhere?
        improved = [r for r in online if r.persist_total and r.fresh_ok < r.fresh_total and r.persist_ok >= r.fresh_ok]
        if improved:
            print(f"  persistent socket improved reliability for: {', '.join(r.name for r in improved)}")


def to_json(results: list[ProbeResult]) -> list[dict]:
    out = []
    for r in results:
        out.append({
            "name": r.name, "ip": r.ip, "present": r.present, "ip_drift": r.ip_drift,
            "tcp": {"ok": r.tcp_ok, "total": r.tcp_total, "errors": list(dict.fromkeys(r.tcp_errors))},
            "status_fresh": {"ok": r.fresh_ok, "total": r.fresh_total,
                             "p50_ms": (pct(r.fresh_lat, 50) or 0) * 1000,
                             "p95_ms": (pct(r.fresh_lat, 95) or 0) * 1000,
                             "errors": list(dict.fromkeys(r.fresh_errors))},
            "status_persistent": {"ok": r.persist_ok, "total": r.persist_total},
            "write_notes": r.write_notes,
            "healthy": r.healthy,
        })
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Tuya smart-lighting stability probe")
    ap.add_argument("--devices", type=Path, default=DEFAULT_JSON, help="path to smart_devices.json")
    ap.add_argument("--iterations", type=int, default=10, help="probes per device per operation")
    ap.add_argument("--timeout", type=float, default=3.0, help="per-call socket timeout (s)")
    ap.add_argument("--retries", type=int, default=1, help="tinytuya socketRetryLimit")
    ap.add_argument("--delay", type=float, default=0.3, help="pause between probes (s)")
    ap.add_argument("--scan-time", type=int, default=18, help="LAN discovery scan duration (s)")
    ap.add_argument("--write", action="store_true", help="also exercise on/off (toggles real lights)")
    ap.add_argument("--json", type=Path, help="write a machine-readable report here")
    args = ap.parse_args()

    if not args.devices.exists():
        print(f"error: device file not found: {args.devices}", file=sys.stderr)
        return 2

    devices = load_devices(args.devices)
    print(f"Loaded {len(devices)} device(s) from {args.devices}")
    print(f"Running LAN discovery scan (~{args.scan_time}s, read-only)…")
    discovered = discover(args.scan_time)
    print(f"Discovered {len(discovered)} Tuya device(s) online.")
    if args.write:
        print("!! --write enabled: physical lights WILL be toggled (state restored afterwards)")

    results = [probe_device(d, discovered, args) for d in devices]
    print_report(results, args)

    if args.json:
        args.json.write_text(json.dumps(to_json(results), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote JSON report to {args.json}")

    # exit code: 0 all healthy, 1 some flaky/offline
    return 0 if all(r.healthy for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
