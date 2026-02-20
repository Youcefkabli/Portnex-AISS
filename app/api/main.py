"""
FastAPI application for Phase 1 AIS API (in-process ingest; no Redis/DB).

- Health: /health/live, /health/ready
- API: /api/v1/live, /api/v1/stats, /api/v1/vessels/{mmsi}
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.health import router as health_router
from app.api.router import router as api_router
from app.services.live_broadcast import LiveBroadcaster
from app.services.ingest_state import set_broadcaster, set_stats, set_vessel_cache
from worker.main import IngestionWorker


def _setup_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()

    broadcaster = LiveBroadcaster(queue_maxsize=1000)
    stats: Dict[str, Any] = {
        "status": "stopped",
        "received": 0,
        "stored": 0,
        "discarded": 0,
        "vessels": 0,
        "errors": 0,
        "dropped": 0,
        "queue_depth_hwm": 0,
    }
    vessel_cache: Dict[int, dict] = {}

    set_broadcaster(broadcaster)
    set_stats(stats)
    set_vessel_cache(vessel_cache)

    worker = IngestionWorker(
        broadcast_callback=broadcaster.broadcast,
        stats=stats,
        vessel_cache=vessel_cache,
    )
    await worker.start()

    yield

    await worker.stop()


app = FastAPI(
    title="AIS API",
    description="Phase 1: real-time vessel positions in a fixed area",
    lifespan=lifespan,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.get("/", include_in_schema=False)
async def live_dashboard():
    """Simple live table dashboard for current API stream."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/map", include_in_schema=False)
async def live_map():
    """Live vessel map dashboard for current API stream."""
    return FileResponse(WEB_DIR / "map.html")


app.include_router(health_router)
app.include_router(api_router, prefix=settings.API_PREFIX)
