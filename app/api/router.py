"""
Phase 1 AIS API: live stream, stats, optional positions/recent.

- GET /live       — SSE stream of current vessel positions (from Redis)
- GET /stats     — basic worker/API counters (from Redis + zone config)
- GET /positions/recent — last N minutes from 1-sec table (debug)
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db
from app.db.schemas import Position, StatsOut
from app.services.redis_client import read_stats, subscribe_live

router = APIRouter()
_UTC = timezone.utc


async def _sse_generator() -> AsyncGenerator[str, None]:
    """
    Subscribe to Redis live channel and yield SSE events.
    Heartbeat every 15s if no message.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1000)
    HEARTBEAT_SEC = 15.0

    async def consume_redis():
        try:
            async for payload in subscribe_live():
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # drop if slow client
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            queue.put_nowait(None)  # signal end

    task = asyncio.create_task(consume_redis())
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SEC)
                if msg is None:
                    break
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@router.get("/live", summary="Live position stream via Server-Sent Events")
async def live_stream():
    """
    Stream of live vessel positions in the configured area.
    Connect with EventSource or: curl -N http://localhost:8000/api/v1/live
    """
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/stats", response_model=StatsOut)
async def stats():
    """Worker stats (from Redis) plus zone config."""
    data = await read_stats()
    if data is None:
        data = {"status": "no worker", "received": 0, "stored": 0, "discarded": 0, "vessels": 0, "errors": 0}
    return StatsOut(
        **data,
        zone_name=settings.ZONE_NAME,
        bbox={
            "lat_min": settings.ZONE_LAT_MIN,
            "lat_max": settings.ZONE_LAT_MAX,
            "lon_min": settings.ZONE_LON_MIN,
            "lon_max": settings.ZONE_LON_MAX,
        },
    )


@router.get("/positions/recent", response_model=list[Position], summary="Recent positions (debug)")
async def recent_positions(
    minutes: int = Query(15, ge=1, le=1440),
    mmsi: Optional[int] = Query(None),
    limit: int = Query(1000, ge=1, le=50_000),
    db: AsyncSession = Depends(get_db),
):
    """Last N minutes from 1-sec table."""
    since = datetime.now(_UTC) - timedelta(minutes=minutes)
    if mmsi:
        sql = "SELECT * FROM positions_1sec WHERE time >= :since AND mmsi = :mmsi ORDER BY time DESC LIMIT :limit"
        params = {"since": since, "mmsi": mmsi, "limit": limit}
    else:
        sql = "SELECT * FROM positions_1sec WHERE time >= :since ORDER BY time DESC LIMIT :limit"
        params = {"since": since, "limit": limit}
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [Position(**dict(r)) for r in rows]
