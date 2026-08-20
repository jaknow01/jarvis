"""
Smart-bulb model and control over the Tuya local protocol.

Connection lifecycle (persistent, good-citizen idle release, IP self-heal) lives in
``lib/tuya_link.py``. This module owns the per-operation policy the owner asked for:
**never hang, never silently drop** — every device call is hard-bounded and every
logical operation retries with a reconnect between attempts, so a cold attempt that
fails is followed by a warm one that succeeds, and the command lands (at the cost of
a little latency) instead of hanging or disappearing. See ``docs/TUYA_LOCAL.md``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, List, Literal

from pydantic import BaseModel, Field, ConfigDict
from tinytuya import BulbDevice

from lib.tuya_link import manager, HARD_TIMEOUT, MAX_ATTEMPTS, DEFAULT_VERSION

logger = logging.getLogger(__name__)


class RGB(BaseModel):
    R: int = Field(..., description="Red channel, 0-255")
    G: int = Field(..., description="Green channel, 0-255")
    B: int = Field(..., description="Blue channel, 0-255")


class Mode(BaseModel):
    mode: Literal["white", "colour"]


class SmartDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    dev_id: str
    ip: str
    local_key: str
    room: str
    zones: List[str]
    port: int = 6668
    version: float = DEFAULT_VERSION

    state: dict = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # bounded, retrying execution against the persistent connection
    # ------------------------------------------------------------------ #
    @staticmethod
    def _locked_call(conn, fn: Callable[[BulbDevice], Any]) -> Any:
        """Run ``fn`` against the bulb while holding the per-device lock."""
        with conn.lock:
            conn.last_used = time.monotonic()
            return fn(conn.bulb)

    @staticmethod
    def _is_error(result: Any) -> bool:
        return isinstance(result, dict) and "Error" in result

    async def _run(self, fn: Callable[[BulbDevice], Any], op_name: str) -> Any:
        """Execute a tinytuya operation with a hard timeout and bounded retries.

        Returns the raw tinytuya result on success, or a structured error dict
        ``{"Error": ..., "reachable": False}`` once all attempts are exhausted.
        Never raises for device/IO failures, and never blocks longer than
        ``HARD_TIMEOUT`` per attempt.
        """
        last_err: Any = None
        healed = False

        for attempt in range(1, MAX_ATTEMPTS + 1):
            conn = manager.get(self.dev_id, self.ip, self.local_key, self.version)
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._locked_call, conn, fn),
                    timeout=HARD_TIMEOUT,
                )
            except asyncio.TimeoutError:
                last_err = "hard-timeout (device accepted no reply in time)"
                logger.warning(f"{self.name}: {op_name} {last_err} [{attempt}/{MAX_ATTEMPTS}]")
                manager.invalidate(self.dev_id)
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                logger.warning(f"{self.name}: {op_name} error {last_err} [{attempt}/{MAX_ATTEMPTS}]")
                manager.invalidate(self.dev_id)
            else:
                if self._is_error(result):
                    last_err = result.get("Error")
                    logger.warning(f"{self.name}: {op_name} device error {last_err} [{attempt}/{MAX_ATTEMPTS}]")
                    manager.invalidate(self.dev_id)
                else:
                    if attempt > 1:
                        logger.info(f"{self.name}: {op_name} recovered on attempt {attempt}")
                    return result

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(0.3 * attempt)  # brief backoff before a warm retry
                # last-resort IP self-heal (once), just before the final attempt
                if attempt == MAX_ATTEMPTS - 1 and not healed:
                    healed = True
                    new_ip = await asyncio.to_thread(manager.rediscover_ip, self.dev_id)
                    if new_ip and new_ip != self.ip:
                        logger.info(f"{self.name}: IP drift {self.ip} -> {new_ip}, self-healed")
                        self.ip = new_ip

        logger.error(f"{self.name}: {op_name} failed after {MAX_ATTEMPTS} attempts ({last_err})")
        return {
            "Error": f"{op_name} failed after {MAX_ATTEMPTS} attempts: {last_err}",
            "reachable": False,
            "device": self.name,
        }

    # ------------------------------------------------------------------ #
    # public operations (interface consumed by lib/tools.py)
    # ------------------------------------------------------------------ #
    def _translate_status(self, state: Any) -> dict:
        if not isinstance(state, dict) or "Error" in state or "dps" not in state:
            self.state = state if isinstance(state, dict) else {"Error": str(state)}
        else:
            params = state["dps"]
            self.state = {
                "is_on": params.get("20"),
                "mode": params.get("21"),
                "brightness": params.get("22", "unknown"),
                "contrast": params.get("23", "unknown"),
            }
        return {"device_info": self.describe_as_json(), "device_state": self.state}

    async def get_status(self) -> dict:
        logger.info(f"Checking status of {self.name}")
        raw = await self._run(lambda b: b.status(), "status")
        return self._translate_status(raw)

    async def turn_on(self) -> dict:
        return await self._set_power(True)

    async def turn_off(self) -> dict:
        return await self._set_power(False)

    async def _set_power(self, on: bool) -> dict:
        op = "turn_on" if on else "turn_off"
        logger.info(f"{self.name}: {op}")
        res = await self._run((lambda b: b.turn_on()) if on else (lambda b: b.turn_off()), op)
        if self._is_error(res):
            return res  # honest failure, not a silent no-op
        return await self._verify_power(on)

    async def _verify_power(self, expected_on: bool) -> dict:
        for _ in range(2):
            status = await self.get_status()
            ds = status.get("device_state", {})
            if ds.get("is_on") == expected_on:
                return {
                    "Success": f"{self.name} is now {'on' if expected_on else 'off'}",
                    "device": self.name,
                    "verified": True,
                    "device_state": ds,
                }
            await asyncio.sleep(0.3)
        return {
            "Warning": f"{self.name}: command sent but new power state could not be confirmed",
            "device": self.name,
            "verified": False,
        }

    async def change_mode(self, new_mode: Mode) -> dict:
        logger.info(f"{self.name}: change mode to {new_mode.mode}")
        res = await self._run(lambda b: b.set_mode(new_mode.mode), "set_mode")
        if self._is_error(res):
            return {"Failed": "Mode change failed", "detail": res.get("Error"), "device": self.name}
        return {"Success": f"Mode set to {new_mode.mode}", "device": self.name}

    async def change_color(self, new_color: RGB) -> dict:
        logger.info(f"{self.name}: change colour to {new_color.R},{new_color.G},{new_color.B}")
        status = await self.get_status()
        ds = status.get("device_state", {})
        if ds.get("Error") or "mode" not in ds:
            return {"Failed": f"{self.name}: couldn't read state before colour change", "detail": ds}
        if ds.get("mode") != "colour":
            return {"Failed": "Device must be in 'colour' mode to change its colour.", "device": self.name}
        res = await self._run(lambda b: b.set_colour(new_color.R, new_color.G, new_color.B), "set_colour")
        if self._is_error(res):
            return {"Failed": "Colour change failed", "detail": res.get("Error"), "device": self.name}
        return {"Success": "New colour has been set", "device": self.name}

    async def change_temperature(self, new_temp: int) -> dict:
        logger.info(f"{self.name}: change temperature to {new_temp}")
        status = await self.get_status()
        ds = status.get("device_state", {})
        if ds.get("Error") or "mode" not in ds:
            return {"Failed": f"{self.name}: couldn't read state before temperature change", "detail": ds}
        if ds.get("mode") != "white":
            return {"Failed": "Device must be in 'white' mode to change its temp.", "device": self.name}
        res = await self._run(lambda b: b.set_colourtemp(new_temp), "set_colourtemp")
        if self._is_error(res):
            return {"Failed": "Temperature change failed", "detail": res.get("Error"), "device": self.name}
        return {"Success": "New lighting temperature has been set", "device": self.name}

    async def prewarm(self) -> dict:
        """Open and warm the connection ahead of a (scheduled) action, so the first
        real command of an unattended action does not pay the cold-start penalty."""
        status = await self.get_status()
        reachable = "Error" not in status.get("device_state", {})
        logger.info(f"{self.name}: prewarm ({'reachable' if reachable else 'unreachable'})")
        return {"prewarmed": self.name, "reachable": reachable}

    # ------------------------------------------------------------------ #
    # helpers / (de)serialization
    # ------------------------------------------------------------------ #
    def describe_as_json(self) -> dict:
        # Only non-sensitive fields are exposed to the model. ip / local_key / dev_id
        # stay server-side (used to open connections), never returned to the LLM.
        return self.model_dump(include={"name", "room", "zones"})

    def get_name(self) -> str:
        return self.name

    @classmethod
    async def create_from_json(cls, json_data: dict) -> "SmartDevice":
        name = json_data["custom_name"]
        params = json_data["params"]
        return cls(
            name=name,
            dev_id=params["id"],
            ip=params["local_ip"],
            local_key=params["local_key"],
            room=params["room"],
            zones=params["zones"],
            version=float(params.get("version", DEFAULT_VERSION)),
        )
