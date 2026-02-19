"""
FastAPI application for Phase 1 AIS API.

- Health: /health/live, /health/ready
- API: /api/v1/live, /api/v1/stats, /api/v1/positions/recent
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.health import router as health_router
from app.api.router import router as api_router
from app.services.redis_client import close_redis


def _setup_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    yield
    await close_redis()


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
