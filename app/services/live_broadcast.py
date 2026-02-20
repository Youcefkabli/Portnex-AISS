"""
In-memory live broadcaster for SSE fanout.

- Each SSE client gets a queue; ingest task broadcasts JSON payloads to all queues.
- No Redis; minimal latency from ingest to client.
"""
import asyncio
from typing import AsyncIterator, Set


class LiveBroadcaster:
    """In-process fanout: broadcast(payload) sends to all subscribed SSE clients."""

    def __init__(self, queue_maxsize: int = 1000):
        self._queues: Set[asyncio.Queue[str]] = set()
        self._queue_maxsize = queue_maxsize
        self._dropped = 0

    def broadcast(self, payload: str) -> None:
        """Send payload to all connected clients. Drops on QueueFull and counts."""
        for q in list(self._queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self._dropped += 1

    async def subscribe_async(self) -> AsyncIterator[str]:
        """
        Subscribe to the live stream. Yields JSON payloads until the consumer stops.
        Remove queue from set when generator is closed.
        """
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._queues.add(q)
        try:
            while True:
                msg = await q.get()
                yield msg
        finally:
            self._queues.discard(q)

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)
