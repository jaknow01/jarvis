#!/usr/bin/env python3
"""
Experiment: does keeping Tuya bulbs "warm" actually improve reliability?

Motivation
----------
The probe (probe.py) showed the flakiness looks like a cold-connection / device
radio-sleep problem. This script tests the fix hypothesis head-to-head, per device,
using ONLY read-only status() calls (it never changes a light).

Two arms, run for each target device:

  COLD arm   (mimics the app today): stay completely idle for `--idle` seconds,
             then do a burst of `--burst` reads, each on a FRESH connection.
             The first read after idle is where a cold-start penalty/failure shows.

  WARM arm   (the proposed design): hold ONE persistent connection and send a
             lightweight status heartbeat every `--heartbeat` seconds during the
             idle window, then do the same burst reusing that warm connection.

We compare first-call-after-idle success + latency, and overall success + latency.

It also runs a CONCURRENCY probe: while holding one local connection, it opens a
second independent connection to port 6668 to see whether the device allows more
than one local client at a time. This sizes the risk of a persistent agent
connection interfering with any *LAN* control by the Tuya app. (The app's normal
path is cloud, which is independent of port 6668 — this only measures local slots.)

Usage (from repo root):
    poetry run python smoke-tests/warm_vs_cold.py
    poetry run python smoke-tests/warm_vs_cold.py --idle 60 --cycles 4 --only Pianino
"""
from __future__ import annotations

import argparse
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe import (  # noqa: E402
    Device, load_devices, discover, run_bounded, is_good_status,
    DEFAULT_JSON, TUYA_PORT,
)


def med(xs):
    return statistics.median(xs) if xs else None


def fmt_ms(x):
    return "n/a" if x is None else f"{x * 1000:.0f}ms"


# --------------------------------------------------------------------------- #
# arms
# --------------------------------------------------------------------------- #
def cold_arm(dev: Device, args):
    """Idle (no contact), then a burst of fresh-connection reads. Repeated."""
    first_ok, first_lat, rest_ok, rest_lat, rest_n = 0, [], 0, [], 0
    for _ in range(args.cycles):
        time.sleep(args.idle)  # genuinely cold: no traffic to the device
        for i in range(args.burst):
            t0 = time.time()

            def read():
                b = dev.bulb(args.timeout, args.retries)
                return b.status()

            st = run_bounded(read, args.timeout * (args.retries + 1) + 1)
            dt = time.time() - t0
            ok = is_good_status(st) if st != "__timeout__" else False
            if i == 0:
                first_ok += int(ok)
                if ok:
                    first_lat.append(dt)
            else:
                rest_n += 1
                rest_ok += int(ok)
                if ok:
                    rest_lat.append(dt)
            time.sleep(0.3)
    return dict(kind="COLD", first_ok=first_ok, first_lat=first_lat,
               rest_ok=rest_ok, rest_lat=rest_lat, rest_n=rest_n, cycles=args.cycles)


def warm_arm(dev: Device, args):
    """Hold a persistent connection, heartbeat during idle, then burst-read it."""
    first_ok, first_lat, rest_ok, rest_lat, rest_n = 0, [], 0, [], 0
    try:
        bulb = dev.bulb(args.timeout, args.retries, persistent=True)
    except Exception:  # noqa: BLE001
        return dict(kind="WARM", error="could not open persistent connection")

    for _ in range(args.cycles):
        # keep warm across the idle window with periodic heartbeats
        waited = 0.0
        while waited < args.idle:
            run_bounded(bulb.status, args.timeout + 1)  # heartbeat (read-only)
            step = min(args.heartbeat, args.idle - waited)
            time.sleep(step)
            waited += step
        for i in range(args.burst):
            t0 = time.time()
            st = run_bounded(bulb.status, args.timeout * (args.retries + 1) + 1)
            dt = time.time() - t0
            ok = is_good_status(st) if st != "__timeout__" else False
            if i == 0:
                first_ok += int(ok)
                if ok:
                    first_lat.append(dt)
            else:
                rest_n += 1
                rest_ok += int(ok)
                if ok:
                    rest_lat.append(dt)
            time.sleep(0.3)
    return dict(kind="WARM", first_ok=first_ok, first_lat=first_lat,
               rest_ok=rest_ok, rest_lat=rest_lat, rest_n=rest_n, cycles=args.cycles)


def concurrency_probe(dev: Device, timeout: float):
    """While holding one local connection, can a second one be opened?"""
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.settimeout(timeout)
    try:
        held.connect((dev.ip, TUYA_PORT))
    except Exception as e:  # noqa: BLE001
        return f"could not open first connection ({type(e).__name__})"
    second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    second.settimeout(timeout)
    try:
        second.connect((dev.ip, TUYA_PORT))
        result = "2nd local connection ACCEPTED (multiple local clients allowed)"
    except Exception as e:  # noqa: BLE001
        result = f"2nd local connection REFUSED ({type(e).__name__}) — single local slot"
    finally:
        second.close()
        held.close()
    return result


# --------------------------------------------------------------------------- #
# per-device driver (run devices in parallel; arms sequential per device)
# --------------------------------------------------------------------------- #
def run_device(dev: Device, args, out: dict):
    conc = concurrency_probe(dev, args.timeout)
    cold = cold_arm(dev, args)
    warm = warm_arm(dev, args)
    out[dev.name] = dict(concurrency=conc, cold=cold, warm=warm)


def summarize(arm):
    if arm.get("error"):
        return f"{arm['kind']:4}  ERROR: {arm['error']}"
    c = arm["cycles"]
    fr = f"{arm['first_ok']}/{c}"
    rr = f"{arm['rest_ok']}/{arm['rest_n']}" if arm["rest_n"] else "0/0"
    return (f"{arm['kind']:4}  first-after-idle {fr} ok (med {fmt_ms(med(arm['first_lat']))})   "
            f"rest {rr} ok (med {fmt_ms(med(arm['rest_lat']))})")


def main() -> int:
    ap = argparse.ArgumentParser(description="warm-vs-cold reliability experiment")
    ap.add_argument("--devices", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--idle", type=float, default=45, help="idle seconds before each burst")
    ap.add_argument("--cycles", type=int, default=3, help="idle+burst cycles per arm")
    ap.add_argument("--burst", type=int, default=4, help="reads per burst")
    ap.add_argument("--heartbeat", type=float, default=12, help="warm-arm heartbeat interval (s)")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--scan-time", type=int, default=12)
    ap.add_argument("--only", action="append", help="restrict to device name(s)")
    args = ap.parse_args()

    devices = load_devices(args.devices)
    if args.only:
        wanted = set(args.only)
        devices = [d for d in devices if d.name in wanted]

    print(f"Discovery scan (~{args.scan_time}s)…")
    discovered = discover(args.scan_time)
    online = [d for d in devices if d.dev_id in discovered]
    offline = [d for d in devices if d.dev_id not in discovered]
    if offline:
        print("Offline (skipped): " + ", ".join(d.name for d in offline))
    if not online:
        print("No online devices to test.")
        return 2

    est = 2 * args.cycles * (args.idle + args.burst) / 60
    print(f"Testing {len(online)} device(s) in parallel. ~{est:.1f} min "
          f"(idle={args.idle}s x cycles={args.cycles} x 2 arms).\n")

    out: dict = {}
    threads = [threading.Thread(target=run_device, args=(d, args, out), daemon=True) for d in online]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("=" * 72)
    print("WARM vs COLD — read-only reliability after idle")
    print("=" * 72)
    for name in [d.name for d in online]:
        r = out.get(name, {})
        print(f"\n{name}")
        print(f"  local-connection slots: {r.get('concurrency')}")
        print(f"  {summarize(r['cold'])}")
        print(f"  {summarize(r['warm'])}")

    print("\n" + "=" * 72)
    print("READ THIS: interpretation")
    print("=" * 72)
    print("- If WARM 'first-after-idle' is markedly better than COLD, keeping devices")
    print("  warm (persistent conn + heartbeat) fixes the cold-start flakiness.")
    print("- If both arms are ~equal and healthy, the devices did not go cold during")
    print("  this window — rerun with a longer --idle to provoke it.")
    print("- 'single local slot' means a persistent agent connection would block a")
    print("  SECOND local client. The Tuya app normally uses the cloud path (not 6668),")
    print("  so it is unaffected — but a good-citizen design should still hold the local")
    print("  socket lightly and release it when idle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
