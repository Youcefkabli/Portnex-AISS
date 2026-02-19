"""
Redis client for live stream fanout and worker stats.

- Worker publishes each position to a channel; API subscribes and streams to SSE clients.
- Worker periodically writes stats to a key; API reads it for GET /stats.
"""
import json
import logging
from typing import Any, AsyncIterator, Optional

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger("ais.redis")

_redis: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _serialize_position(row: dict[str, Any]) -> str:
    """JSON-serialise position; datetime -> ISO string."""
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return json.dumps(out)


async def publish_live_position(row: dict[str, Any]) -> None:
    """Publish one position event to the live channel (worker calls this)."""
    r = await get_redis()
    payload = _serialize_position(row)
    await r.publish(settings.REDIS_LIVE_CHANNEL, payload)


async def write_stats(stats: dict[str, Any]) -> None:
    """Write worker stats to Redis (worker calls this periodically)."""
    r = await get_redis()
    await r.set(settings.REDIS_STATS_KEY, json.dumps(stats), ex=60)


async def read_stats() -> Optional[dict[str, Any]]:
    """Read worker stats from Redis (API calls this for /stats)."""
    r = await get_redis()
    raw = await r.get(settings.REDIS_STATS_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


async def subscribe_live() -> AsyncIterator[str]:
    """
    Subscribe to the live position channel; yields JSON strings (API uses this for SSE).
    """
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(settings.REDIS_LIVE_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message" and message.get("data"):
                yield message["data"]
    finally:
        await pubsub.unsubscribe(settings.REDIS_LIVE_CHANNEL)
        await pubsub.close()
