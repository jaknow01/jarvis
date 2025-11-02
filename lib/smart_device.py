from tinytuya import BulbDevice
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal
from asyncio import wait_for, to_thread

class RGB(BaseModel):
    R: int = Field(..., description="Red channel, 0-255")
    G: int = Field(..., description="Green channel, 0-255")
    B: int = Field(..., description="Blue channel, 0-255")

class Mode(BaseModel):
    mode: Literal["white", "colour"]

TIMEOUT = 3
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

        state = await self._check_status()
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

    async def _check_status(self) -> dict:
        try:
            device = await self._create_device()
            status = await wait_for(
                to_thread(device.status),
                timeout=TIMEOUT
            )
        except Exception as e:
            status = {"Error":"Timeout: device is not responding"}
            print(e)

        return status
    
    async def _is_responding(self, state: dict) -> bool:
        return "Error" not in state

    async def turn_on(self):
        state = await self._check_status()
        if await self._is_responding(state):
            device = await self._create_device()
            device.turn_on()

    async def turn_off(self):
        state = await self._check_status()
        if await self._is_responding(state):
            device = await self._create_device()
            device.turn_off()

    async def change_color(self, new_color: RGB) -> dict:
        state = await self.get_status()
        if await self._is_responding(state):
            device = await self._create_device()
            if state['device_state']["mode"] == "colour":
                device.set_colour(new_color.R, new_color.G, new_color.B)
                return {"Success": "New colour has been set"}
            else:
                return {"Failed": "Device must be in 'colour' mode to change its colour."}

    async def change_mode(self, new_mode: Mode):
        state = await self._check_status()
        if await self._is_responding(state):
            device = await self._create_device()
            device.set_mode(new_mode.mode)

    async def change_temperature(self, new_temp: int) -> dict:
        state = await self.get_status()

        if await self._is_responding(state):
            device = await self._create_device()
            if state['device_state']["mode"] == "white":
                device.set_colourtemp(new_temp)
                return {"Success": "New lighting temperature has been set"}
            else:
                return {"Failed": "Device must be in 'white' mode to change its temp."}

    def describe_as_json(self) -> dict:
        return self.model_dump(exclude={"device", "state"})
    
    def get_name(self) -> str:
        return self.name

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
    



