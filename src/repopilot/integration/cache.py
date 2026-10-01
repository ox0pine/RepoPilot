from __future__ import annotations

import hashlib
import json

from redis.asyncio import Redis
from redis.exceptions import RedisError


class ModelCache:
    def __init__(self, client: Redis, ttl_seconds: int = 300) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(base_url: str, api_key: str) -> str:
        digest = hashlib.sha256(json.dumps([base_url, api_key]).encode()).hexdigest()
        return f"repopilot:models:{digest}"

    async def get(self, base_url: str, api_key: str) -> list[str] | None:
        try:
            value = await self.client.get(self.key(base_url, api_key))
            if value is None:
                return None
            payload = json.loads(value)
            if isinstance(payload, list) and all(
                isinstance(item, str) and item.strip() for item in payload
            ):
                return payload
            return None
        except (RedisError, TypeError, ValueError, UnicodeError):
            return None

    async def set(self, base_url: str, api_key: str, models: list[str]) -> None:
        try:
            await self.client.set(self.key(base_url, api_key), json.dumps(models), ex=self.ttl_seconds)
        except (RedisError, TypeError, ValueError, UnicodeError):
            return
