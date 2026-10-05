"""Redis atomic rolling admission, revocation, single-flight and publish fences.

All keys share an account hash slot. Identity/fence survive payload expiry;
no plaintext prompt, title or source excerpt is written to Redis or job arguments.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from orchestrator.home_suggestions.contracts import CACHE_SECONDS, LEASE_SECONDS, canonical
from orchestrator.memory.encryption import ContentEncryption

SYNC = """
local old = tonumber(redis.call('HGET', KEYS[1], 'epoch') or '-1')
local epoch = tonumber(ARGV[1])
if epoch < old then return 0 end
if epoch > old then
  redis.call('DEL', KEYS[2], KEYS[3], KEYS[4])
  redis.call('HSET', KEYS[1], 'epoch', ARGV[1], 'enabled', ARGV[2])
elseif redis.call('HGET', KEYS[1], 'enabled') ~= ARGV[2] then
  return 0
end
return 1
"""

ADMIT = """
if redis.call('HGET', KEYS[1], 'epoch') ~= ARGV[1] or
   redis.call('HGET', KEYS[1], 'enabled') ~= '1' then return 'disabled' end
if ARGV[4] == '0' and redis.call('HGET', KEYS[4], 'fingerprint') == ARGV[3] then
  return 'unchanged'
end
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now - 3600000)
if redis.call('ZCARD', KEYS[5]) >= 4 then return 'limited' end
redis.call('ZADD', KEYS[5], now, ARGV[2])
redis.call('PEXPIRE', KEYS[5], 3600001)
if redis.call('EXISTS', KEYS[3]) == 1 then return 'unchanged' end
redis.call('SET', KEYS[3], ARGV[2], 'EX', ARGV[5])
redis.call('DEL', KEYS[2])
redis.call('HSET', KEYS[4], 'fingerprint', ARGV[3], 'status', 'generating', 'epoch', ARGV[1])
return 'queued'
"""

PUBLISH = """
if redis.call('HGET', KEYS[1], 'epoch') ~= ARGV[1] or
   redis.call('HGET', KEYS[1], 'enabled') ~= '1' or
   redis.call('GET', KEYS[3]) ~= ARGV[2] then return 0 end
redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[4])
redis.call('HSET', KEYS[4], 'status', ARGV[5])
redis.call('DEL', KEYS[3])
return 1
"""

RELEASE = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[3])
redis.call('HSET', KEYS[4], 'status', 'error')
return 1
"""

CLAIM = """
if redis.call('HGET', KEYS[1], 'epoch') ~= ARGV[1] or
   redis.call('HGET', KEYS[1], 'enabled') ~= '1' or
   redis.call('GET', KEYS[2]) ~= ARGV[2] then return 0 end
return redis.call('SET', KEYS[3], 'claimed', 'NX', 'EX', ARGV[3]) and 1 or 0
"""


def text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class SuggestionCache:
    def __init__(self, redis: Any, encryption: ContentEncryption, user_id: uuid.UUID) -> None:
        self.redis = redis
        self.encryption = encryption
        self.prefix = f"home-suggestions:{{{user_id}}}"
        self.keys = [
            f"{self.prefix}:{suffix}"
            for suffix in ("fence", "payload", "lease", "identity", "attempts")
        ]

    async def sync(self, enabled: bool, epoch: int) -> bool:
        return bool(
            await self.redis.eval(SYNC, 4, *self.keys[:4], str(epoch), "1" if enabled else "0")
        )

    async def admit(self, epoch: int, fingerprint: str, manual: bool) -> tuple[str, str]:
        token = uuid.uuid4().hex
        status = await self.redis.eval(
            ADMIT,
            5,
            *self.keys,
            str(epoch),
            token,
            fingerprint,
            "1" if manual else "0",
            LEASE_SECONDS,
        )
        return text(status), token

    async def read(self) -> tuple[dict[str, Any] | None, Any]:
        # MGET gives payload and lease one atomic observation. Identity contains
        # no private source data and cannot authorize a payload by itself.
        raw, lease = await self.redis.mget(self.keys[1], self.keys[2])
        if raw is None:
            return None, lease
        if len(raw) > 400000:
            raise ValueError("Oversized encrypted home cache")
        payload = json.loads(self.encryption.decrypt(text(raw)))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Invalid home cache")
        return payload, raw

    async def identity(self) -> dict[str, str]:
        return {
            text(key): text(value)
            for key, value in (await self.redis.hgetall(self.keys[3])).items()
        }

    async def publish(self, epoch: int, token: str, payload: dict[str, Any], status: str) -> bool:
        encrypted = self.encryption.encrypt(canonical(payload))
        return bool(
            await self.redis.eval(
                PUBLISH, 4, *self.keys[:4], str(epoch), token, encrypted, CACHE_SECONDS, status
            )
        )

    async def release(self, token: str) -> None:
        await self.redis.eval(RELEASE, 4, *self.keys[:4], token)

    async def lease_valid(self, epoch: int, token: str) -> bool:
        fence = await self.redis.hgetall(self.keys[0])
        state = {text(key): text(value) for key, value in fence.items()}
        return (
            state == {"epoch": str(epoch), "enabled": "1"}
            and text(await self.redis.get(self.keys[2])) == token
        )

    async def lease_seconds_remaining(self) -> float:
        return max(0.0, float(await self.redis.pttl(self.keys[2])) / 1000.0)

    async def claim(self, epoch: int, raw: Any, candidate_id: str) -> bool:
        return bool(
            await self.redis.eval(
                CLAIM,
                3,
                self.keys[0],
                self.keys[1],
                f"{self.prefix}:claim:{candidate_id}",
                str(epoch),
                raw,
                CACHE_SECONDS,
            )
        )
