"""Phase 1 API response schemas."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class Position(BaseModel):
    """Single position (1-sec or 1-min)."""
    model_config = ConfigDict(from_attributes=True)

    time: datetime
    mmsi: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed: Optional[float] = None
    course: Optional[float] = None
    heading: Optional[int] = None
    nav_status: Optional[int] = None
    rot: Optional[float] = None
    msg_type: Optional[int] = None
    msg_count: Optional[int] = None


class StatsOut(BaseModel):
    """Ingestion/worker stats (from Redis or worker)."""
    status: str = "unknown"
    received: int = 0
    stored: int = 0
    discarded: int = 0
    vessels: int = 0
    errors: int = 0
    zone_name: str = ""
    bbox: dict[str, float] = {}
