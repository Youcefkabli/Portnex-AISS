"""
Phase 1 AIS API: live stream, stats, vessels (in-memory; no Redis/DB).

- GET /live       — SSE stream of current vessel positions (from in-process broadcaster)
- GET /stats      — worker stats + zone bbox (from in-memory)
- GET /vessels/{mmsi} — static vessel details from in-memory cache
"""
import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.db.schemas import StatsOut, VesselOut
from app.services.ingest_state import get_broadcaster, get_stats, get_vessel_cache

router = APIRouter()
_sse_dropped_total: int = 0


async def _sse_generator() -> AsyncGenerator[str, None]:
    """
    Subscribe to in-memory broadcaster and yield SSE events.
    Heartbeat every 15s if no message.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1000)
    HEARTBEAT_SEC = 15.0

    async def consume_broadcaster():
        global _sse_dropped_total
        try:
            broadcaster = get_broadcaster()
            async for payload in broadcaster.subscribe_async():
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    _sse_dropped_total += 1
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(consume_broadcaster())
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
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/stats", response_model=StatsOut)
async def stats():
    """Worker stats (in-memory) plus zone config. sse_dropped from broadcaster."""
    data = get_stats().copy()
    broadcaster = get_broadcaster()
    data.setdefault("dropped", 0)
    data["sse_dropped"] = _sse_dropped_total + broadcaster.dropped
    lon_west = min(settings.ZONE_LON_MIN, settings.ZONE_LON_MAX)
    lon_east = max(settings.ZONE_LON_MIN, settings.ZONE_LON_MAX)
    return StatsOut(
        **data,
        zone_name=settings.ZONE_NAME,
        bbox={
            "lat_min": settings.ZONE_LAT_MIN,
            "lat_max": settings.ZONE_LAT_MAX,
            "lon_min": lon_west,
            "lon_max": lon_east,
        },
    )


@router.get("/vessels/{mmsi}", response_model=VesselOut, summary="Static vessel details by MMSI")
async def vessel_by_mmsi(mmsi: int):
    """Fetch one vessel static/voyage record from in-memory cache (from stream)."""
    cache = get_vessel_cache()
    vessel = cache.get(mmsi)
    if vessel is None:
        raise HTTPException(status_code=404, detail="Vessel not found")
    # VesselOut expects first_seen; cache has last_updated only
    out = dict(vessel)
    out.setdefault("first_seen", vessel.get("last_updated"))
    return VesselOut.model_validate(out)
