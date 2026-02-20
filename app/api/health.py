"""Health endpoints: liveness and readiness."""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness():
    """Liveness: process is running. No dependencies checked."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness():
    """Readiness: no external deps (in-memory ingest)."""
    return {"status": "ok"}
