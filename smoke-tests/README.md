# Smoke tests — smart-lighting (Tuya) stability

This folder exists to answer a concrete question that has dogged the project from
the start: **why is talking to the smart bulbs sometimes reliable and sometimes
not?** The `iot_operator` agent drives Tuya bulbs over the LAN via
[`tinytuya`](https://github.com/jasonacox/tinytuya), which uses the devices' *local*
protocol (the "side door" — local key + port 6668, protocol 3.3). That local path
is fast but finicky, and the flakiness was never characterized. `probe.py` measures
it systematically.

> These are **diagnostic harnesses that hit real hardware**, not CI unit tests.
> They must run on a machine on the **same LAN** as the bulbs. They are kept out of
> the `pytest` suite on purpose.

## Running it

From the repo root (needs the device inventory at
`data/smart_device_data/smart_devices.json`):

```bash
# read-only: discovery + TCP reachability + status() reads (does NOT change lights)
poetry run python smoke-tests/probe.py

# more samples, machine-readable output
poetry run python smoke-tests/probe.py --iterations 20 --json smoke-tests/last-report.json

# also exercise on/off (physically toggles lights, restores original state)
poetry run python smoke-tests/probe.py --write
```

Key flags: `--iterations` (samples per device per op), `--timeout` (per-call socket
timeout), `--retries` (tinytuya `socketRetryLimit`), `--scan-time` (discovery
duration), `--write` (opt-in state changes). Exit code is `0` only if every device
is healthy, else `1`.

## What it measures, and why each matters

| Probe | What it tells us |
|-------|------------------|
| **LAN discovery scan** (UDP broadcast) | Which devices are actually online right now, and their *current* IP — so we can detect **IP drift** (DHCP moved a device, making the stored IP stale). |
| **TCP :6668 reachability** | Whether the device accepts a socket at all. A device can answer broadcast discovery yet refuse TCP — a distinct, important failure mode. |
| **`status()` reads, fresh socket** | Reliability + latency of the pattern the app uses today (a brand-new `BulbDevice` per call). Reads do not change the light. |
| **`status()` reads, persistent socket** | Same, but reusing one connection — the head-to-head that shows whether connection reuse fixes the flakiness. |
| **write ops** (`--write`) | Whether on/off/color/temp specifically are less reliable than reads. |

Every device call runs under a **hard wall-clock timeout in a worker thread**, so a
single hung bulb can't stall the whole run — which matters, because "accepts the
connection but never replies" is one of the failure modes seen here.

## Why the current code is flaky — analysis of `lib/smart_device.py`

The measurements (below) line up with clear anti-patterns in how the app talks to
the bulbs today:

1. **A fresh connection on every call — often two.** Every operation builds a new
   `BulbDevice` (`_create_device()`), and the mutating methods
   (`turn_on`/`turn_off`/`change_color`/`change_mode`/`change_temperature`) first
   call `_check_status()` (one connection) and then create *another* device to act
   (a second connection). Tuya's **cold** (first-after-idle) connection is exactly
   the unreliable one, so the code maximizes the number of cold connects.
2. **No persistent socket.** tinytuya supports `set_socketPersistent(True)` to keep
   one TCP connection open and reuse it — markedly faster and more reliable. The app
   never uses it.
3. **Conflicting timeout layers.** Reads are wrapped in `asyncio.wait_for(..., 3s)`
   while tinytuya runs *its own* retry/timeout loop underneath. The 3 s asyncio cap
   can fire in the middle of a tinytuya retry and report a spurious timeout for a
   device that would have answered.
4. **Inconsistent timeouts.** `get_status()` is bounded by `wait_for`, but the write
   methods are **not** — so a wedged device (see Pianino below) can block a write
   indefinitely instead of failing fast.
5. **No self-healing for IP drift.** IPs come only from the JSON. If DHCP reassigns a
   bulb, every call fails until someone re-scans and edits the file by hand.
6. **Fire-and-forget writes.** `turn_on`/`turn_off`/`change_mode` return `None` and
   don't verify the new state; confirmation happens (if at all) via a separate
   status round-trip in the tool layer.

## Recommendations (what to actually do)

- **Reuse connections.** Hold one `BulbDevice` per device for the process lifetime
  with `set_socketPersistent(True)`; don't rebuild it per call. This alone should
  remove most "sometimes works, sometimes not" cold-connect failures.
- **Own the timeout in one place.** Set `set_socketTimeout(2–3s)` +
  `set_socketRetryLimit(1)` on the bulb, and wrap **all** device I/O (reads *and*
  writes) in a single bounded `to_thread` + `wait_for` so nothing can hang.
- **Fail fast on wedged devices.** A device that refuses TCP should be marked
  unreachable quickly and surfaced to the user, not retried into a multi-second
  stall.
- **Self-heal IP drift.** On failure, run a `deviceScan`, match by device-id, update
  the IP, and retry once before giving up.
- **Verify writes.** Read state back after a change (with one short retry) and report
  success/failure honestly.

See `probe.py` for the harness and `last-report.json` for the most recent raw run.

## Measured results (2026-08-19, 6 devices, from 192.168.0.162 on the same LAN)

Regenerate anytime with
`poetry run python smoke-tests/probe.py --json smoke-tests/last-report.json`.
Raw output for this snapshot is in `last-run.txt` / `last-report.json`.

| Device | Online | TCP :6668 | status (fresh) | status (persistent) |
|--------|--------|-----------|----------------|---------------------|
| Telewizor | yes | 5/5 | 5/5, p50 28 ms | 5/5, p50 21 ms |
| Łóżko | yes | 5/5 | 5/5, **p50 211 ms** | 5/5, **p50 105 ms** |
| Kinkiet prawy | yes | 5/5 | 5/5, p50 25 ms | 5/5, p50 18 ms |
| Kinkiet lewy | yes | 5/5 | 5/5, p50 23 ms | 5/5, p50 15 ms |
| Lampa stojąca | **no** | — | — (offline / powered off) | — |
| Pianino | yes | 5/5 | 5/5, p50 217 ms | 5/5, p50 108 ms |

### The instability, caught in the act

This snapshot is stable, but it was **not** stable a few minutes earlier, with
nothing changed in between:

- **First contact (cold):** a plain TCP connect to *every* device returned
  `EHOSTUNREACH` ("No route to host"), even though a broadcast scan immediately
  afterwards found 5 of them at those exact IPs. The devices' radios/ARP were cold;
  the scan woke them.
- **Pianino specifically:** in the run just before this one it was **0/5 on TCP and
  `status()` hung until a hard timeout**. In this run it is **5/5**. Same device,
  same config, minutes apart. That flip *is* the "sometimes works, sometimes not"
  the project has always hit.

### What the numbers say

1. **The flakiness is a cold-connection / per-device-wake problem, not stale IPs.**
   Discovery confirmed every online device is at the IP stored in the JSON (no drift
   this time), yet direct TCP still failed while cold. Reusing a warm connection is
   the lever, not re-scanning IPs.
2. **Persistent sockets are faster everywhere and help the marginal devices most.**
   The slow bulbs (Łóżko, Pianino, both ~210 ms fresh) drop to ~105 ms persistent —
   roughly half the time is connection setup. Those same marginal devices are the
   ones that flip offline, so cutting their per-call cost also shrinks the window in
   which a call can fail.
3. **There are two latency tiers.** ~20–30 ms (Telewizor, Kinkiety — strong signal)
   vs ~210 ms (Łóżko, Pianino — weak signal / far from the AP). High latency is a
   good predictor of which devices will be unreliable; worth surfacing as a health
   signal and possibly worth a WiFi/placement fix on the hardware side.
4. **A wedged device blocks the app, not just itself.** When Pianino refused TCP, the
   app's write path (no `wait_for`) would hang on it. The harness only survived
   because every call is wrapped in a hard thread timeout — which is precisely the
   guard the production code is missing.

Net: the single highest-value change is **persistent, reused connections with one
consistently-enforced timeout**, plus fast-failing and flagging devices that are
cold/wedged. See the recommendations above.
