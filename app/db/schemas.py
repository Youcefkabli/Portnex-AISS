"""Phase 1 API response schemas."""
from datetime import datetime
from typing import Optional

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
    dropped: int = 0
    zone_name: str = ""
    bbox: dict[str, float] = {}
    # Backpressure / observability (additive, optional)
    queue_depth_hwm: int = 0
    publish_queue_hwm: int = 0
    last_flush_count: int = 0
    last_flush_duration_ms: int = 0
    sse_dropped: int = 0


class VesselOut(BaseModel):
    """Static vessel and voyage data."""
    model_config = ConfigDict(from_attributes=True)

    mmsi: int
    imo: Optional[int] = None
    callsign: Optional[str] = None
    name: Optional[str] = None
    ship_type: Optional[int] = None
    ship_type_text: Optional[str] = None
    dim_to_bow: Optional[int] = None
    dim_to_stern: Optional[int] = None
    dim_to_port: Optional[int] = None
    dim_to_starboard: Optional[int] = None
    draught: Optional[float] = None
    destination: Optional[str] = None
    eta: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_updated: Optional[datetime] = None
