import tinytuya
from enum import Enum

class RGB():
    def __init__(self, r: int, g: int, b: int):
         self.r = r
         self.g = g
         self.b = b

class ColorMode(Enum):
    WHITE = "white"
    COLOR = "colour"


class SmartDevice():

    def __init__ (self,
                        name: str,
                        dev_id: str,
                        ip: str,
                        local_key: str,
                        room: str,
                        zones: list[str],
                        port: int = 6668):
        self.name = name
        self.dev_id = dev_id
        self.ip = ip
        self.local_key = local_key
        self.room = room
        self.zones = zones
        self.port = port

        self.state = {}

        self.device = tinytuya.BulbDevice(dev_id=dev_id, address=ip, local_key=local_key, port=port, version=3.3)

    async def get_status(self) -> dict:
        print(f"Chcecking status of {self.name}")
        state = self.device.status()
        state_translated = {}

        if "Error" in state.keys():
            self.state = state
        else:
            params = state["dps"]
            state_translated["is_on"] = params["20"]
            state_translated["mode"] = params["21"]
            state_translated["brightness"] = params["22"] if "22" in params.keys() else "unknown"
            state_translated["contrast"] = params["23"] if "23" in params.keys() else "unknown"
            self.state = state_translated

        device_info = await self.describe_as_json()

        full_state = {"device_info" : device_info, "device_state": self.state}
        
        return full_state

    async def _check_status(self) -> bool:
        return "Error" in self.state
    
    async def turn_on(self):
        if await self._check_status():
            self.device.turn_on()

    async def turn_off(self):
        if await self._check_status():
            self.device.turn_off()

    async def change_color(self, new_color: RGB):
        if await self._check_status():
            self.device.set_colour(new_color.r, new_color.g, new_color.b)

    async def change_mode(self, new_mode: ColorMode):
        if await self._check_status():
            self.device.set_mode(new_mode.value)

    async def describe_as_json(self) -> dict:
        return {
            "name" : self.name,
            "device_id" : self.dev_id,
            "local_ip" : self.ip,
            "local_key" : self.local_key,
            "room" : self.room,
            "zones" : self.zones
        }
      
    @classmethod
    async def from_json(self, json_data: dict):
        name = json_data["custom_name"]
        params = json_data["params"]

        return SmartDevice(name=name, 
                           dev_id=params["id"], 
                           ip=params["local_ip"], 
                           local_key=params["local_key"],
                           room=params["room"],
                           zones=params["zones"]
                        )
    



