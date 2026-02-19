"""Health endpoints: liveness and readiness."""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.database import AsyncSessionLocal
from app.services.redis_client import get_redis

router = APIRouter(tags=["health"])
logger = logging.getLogger("ais.health")


@router.get("/health/live")
async def liveness():
    """Liveness: process is running. No dependencies checked."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness():
    """Readiness: DB and Redis are reachable."""
    errors = []
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("DB readiness check failed: %s", e)
        errors.append("database")

    try:
        r = await get_redis()
        await r.ping()
    except Exception as e:
        logger.warning("Redis readiness check failed: %s", e)
        errors.append("redis")

    if errors:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "errors": errors},
        )
    return {"status": "ok"}
