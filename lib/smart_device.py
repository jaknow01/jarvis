from tinytuya import BulbDevice
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict
from typing import List
from asyncio import wait_for, to_thread

class RGB():
    def __init__(self, r: int, g: int, b: int):
         self.r = r
         self.g = g
         self.b = b

class ColorMode(Enum):
    WHITE = "white"
    COLOR = "colour"

TIMEOUT = 4
RETRIES = 3

class SmartDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    dev_id: str
    ip: str
    local_key: str
    room: str
    zones: List[str]
    port: int = 6668

    # device: BulbDevice | None = None
    state: dict = Field(default_factory=dict) 

    async def _create_device(self):
        return BulbDevice(
            dev_id=self.dev_id, 
            address=self.ip, 
            local_key=self.local_key, 
            port=self.port, 
            version=3.3
        )

    async def get_status(self) -> dict:
        print(f"Checking status of {self.name}")

        state = await self._check_status(full_status=True)
        state_translated = {}

        if "Error" in state.keys():
            self.state = state
        else:
            params = state["dps"]
            state_translated["is_on"] = params.get("20")
            state_translated["mode"] = params.get("21")
            state_translated["brightness"] = params.get("22", "unknown")
            state_translated["contrast"] = params.get("23", "unknown")
            self.state = state_translated

        device_info = self.describe_as_json()
        return {"device_info": device_info, "device_state": self.state}

    async def _check_status(self, full_status: bool = False) -> bool | dict:
        try:
            device = await self._create_device()
            status = await wait_for(
                to_thread(device.status),
                timeout=TIMEOUT
            )
        except Exception as e:
            status = {"Error":"Timeout: device is not responding"}
            print(e)

        return status if full_status else "Error" not in status

    async def turn_on(self):
        if await self._check_status():
            device = await self._create_device()
            device.turn_on()

    async def turn_off(self):
        if await self._check_status():
            device = await self._create_device()
            device.turn_off()

    async def change_color(self, new_color):
        if await self._check_status():
            device = await self._create_device()
            device.set_colour(new_color.r, new_color.g, new_color.b)

    async def change_mode(self, new_mode):
        if await self._check_status():
            device = await self._create_device()
            device.set_mode(new_mode.value)

    def describe_as_json(self) -> dict:
        return self.model_dump(exclude={"device", "state"})

    @classmethod
    async def create_from_json(cls, json_data: dict):
        name = json_data["custom_name"]
        params = json_data["params"]
        return cls(
            name=name,
            dev_id=params["id"],
            ip=params["local_ip"],
            local_key=params["local_key"],
            room=params["room"],
            zones=params["zones"]
        )
    



