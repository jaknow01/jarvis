# import aioredis
from typing import Any
import redis.asyncio as redis
from lib.smart_device import SmartDevice


class Cache():
    def __init__(self, ttl:int = 3600 * 24 * 5):
        self.redis_cache = redis.from_url(
            # url = "redis://redis:6379" # only if running code in docker
            url = "redis://localhost:6379",
            decode_responses = True
        )
        self.ttl = ttl

    async def save_to_cache(self, key: str, val: Any):
        """Save to redis with a given key"""
        await self.redis_cache.set(key, val)

    async def get_from_cache(self, key: str) -> Any:
        """Retrieves information from cache"""
        data = await self.redis_cache.get(key)
        return data

class Ctx():
    def __init__(self, cache: Cache = Cache()):
        self.cache = cache
        self.devices: dict[str, SmartDevice] = {}