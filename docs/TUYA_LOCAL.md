# Talking to the smart lights (Tuya local protocol) — design & decisions

This document records **why** the smart-lighting layer (`lib/smart_device.py` +
`lib/tuya_link.py`) is built the way it is. It is the outcome of the stability
investigation in [`smoke-tests/`](../smoke-tests/README.md); read that for the raw
measurements. This file is the "so we decided…" companion.

## Background: the "side door"

The `iot_operator` agent controls Tuya smart bulbs through
[`tinytuya`](https://github.com/jasonacox/tinytuya), which speaks the devices'
**local** protocol: a TCP server on each bulb at port `6668`, protocol 3.3,
authenticated with a per-device local key. This is fast (LAN, no cloud round-trip)
but finicky — and that finickiness was the original pain point of the whole project.

The Tuya **phone app**, by contrast, controls the bulbs through the **Tuya cloud**
(device ⇄ cloud ⇄ app). That path is independent of local port 6668.

> **Primary vs. secondary usage.** The phone app (cloud) is the owner's main way of
> controlling the lights. The agent (local) is *additional*. The design below is
> deliberately a "good citizen": it must never degrade the app path.

## What the investigation found

(Full data: `smoke-tests/README.md`. Measured 2026-08-19, 6 devices.)

1. **The flakiness is a cold-connection / device-wake problem, not stale IPs.** On
   first (cold) contact, direct TCP to every device failed with `EHOSTUNREACH`
   even though a broadcast scan saw them at the stored IPs; after a scan woke them
   they were healthy. One device (Pianino) flipped from `0/5` with a hung `status()`
   to `5/5` minutes later with no change — the "sometimes works, sometimes not" in
   the act.
2. **A persistent, reused connection is faster everywhere and helps the weak devices
   most.** Warm vs cold experiment: on the two marginal (weak-signal) bulbs latency
   dropped ~2.6× (≈210 ms → ≈80 ms). The ~130 ms difference is the TCP handshake that
   a fresh-connection-per-call design pays every single time.
3. **A wedged device could hang the whole app.** The old code wrapped *reads* in a
   3 s `wait_for` but left *writes* unbounded, so a device that accepts a socket but
   never replies could block a command indefinitely.
4. **Multiple local connections are allowed.** Every device accepted a second
   simultaneous local connection. Combined with the app using the cloud path, an
   agent holding a persistent local connection does **not** block the app.

## Decisions

### D1 — Reuse one persistent connection per device (not fresh-per-call)
`set_socketPersistent(True)`, opened lazily on first use and cached in a
process-wide manager (`lib/tuya_link.py`). This removes the cold TCP handshake from
every command — the biggest measured win.

### D2 — "Good citizen", not "always warm"
The cached connection is **released after `IDLE_RELEASE` seconds of no use** (a small
daemon reaper closes idle sockets). We do **not** hold connections or heartbeat 24/7.

*Why not always-warm?* It maximizes the agent's footprint on exactly the devices that
are already marginal (weak firmware, weak signal), risks destabilizing cheap Tuya
firmware / its cloud link under constant local polling, adds always-on background
machinery, and buys latency we don't need — the agent is used occasionally, not in a
real-time loop. Good-citizen keeps connections warm *during an active burst of
commands* (where it matters) and lets go when idle. This aligns with "the app is
primary, the agent is secondary."

### D3 — Latency is acceptable; hanging and dropping are not
Owner's requirement: paying a cold-start delay is fine **as long as the light
ultimately responds**. A long hang or a silently dropped command is not. Therefore:

- **Every device call is hard-bounded** (`asyncio.wait_for` over `to_thread`), so a
  wedged device can never hang a command — it fails fast instead.
- **Every logical operation retries** (bounded, with reconnect between attempts).
  This turns the observed "cold attempt fails → warm attempt succeeds" into automatic
  recovery, so the command lands instead of dropping. The user-visible cost is a bit
  of extra latency on a cold device — the accepted trade.

### D4 — Self-healing IP as a last resort
If retries still fail, run one bounded broadcast `deviceScan`, match by device id, and
if the IP moved, update it and retry once. Cheap insurance against DHCP drift.

### D5 — Verify writes; report failures honestly
After `turn_on/off`/color/temperature, read the state back (with a short retry) and
confirm it changed. On failure the layer returns a **structured error**, never a
silent no-op — the coordinator is instructed to tell the user plainly when a device
didn't respond.

### D6 — Pre-warm hook for proactive use
`prewarm()` opens the connection ahead of time. Intended for scheduled/cron actions
("turn on the lights at sunset") so the first command of an unattended action doesn't
pay the cold penalty — the benefit of always-warm, only when actually needed.

## Tunables (`lib/tuya_link.py`)

| Constant | Default | Meaning |
|----------|---------|---------|
| `SOCKET_TIMEOUT` | 3.0 s | per-call tinytuya socket timeout |
| `RETRY_LIMIT` | 1 | tinytuya's own internal retry count |
| `HARD_TIMEOUT` | 8.0 s | absolute cap per call (`wait_for`); guarantees no hang |
| `MAX_ATTEMPTS` | 3 | bounded reconnect-and-retry per operation |
| `IDLE_RELEASE` | 120 s | close a connection unused for this long (good citizen) |
| `REAP_INTERVAL` | 30 s | how often the idle reaper runs |
| `SCAN_TIMEOUT` | 8 s | bounded discovery scan for IP self-heal |

## Explicitly out of scope (for now)

- Holding connections warm 24/7 (rejected — see D2).
- Cloud/TuyaAPI control from the agent (the app owns the cloud path).
- Re-keying / device pairing (done once via the Tuya app / tinytuya wizard).

If a device is genuinely wedged at the firmware level (accepts TCP, never replies),
no client-side change fixes it — the layer fails fast and flags it, and the fix is a
power-cycle of that bulb. That is surfaced to the user rather than hidden.
