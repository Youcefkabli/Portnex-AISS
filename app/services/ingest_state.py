"""
Shared in-memory state for in-process ingest (stats, vessel cache).

Set at app lifespan start; read by API routes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.live_broadcast import LiveBroadcaster

_broadcaster: Optional[LiveBroadcaster] = None
_stats: Optional[Dict[str, Any]] = None
_vessel_cache: Optional[Dict[int, dict]] = None


def set_broadcaster(b: LiveBroadcaster) -> None:
    global _broadcaster
    _broadcaster = b


def get_broadcaster() -> LiveBroadcaster:
    if _broadcaster is None:
        raise RuntimeError("Ingest state not initialized")
    return _broadcaster


def set_stats(s: Dict[str, Any]) -> None:
    global _stats
    _stats = s


def get_stats() -> Dict[str, Any]:
    if _stats is None:
        raise RuntimeError("Ingest state not initialized")
    return _stats


def set_vessel_cache(c: Dict[int, dict]) -> None:
    global _vessel_cache
    _vessel_cache = c


def get_vessel_cache() -> Dict[int, dict]:
    if _vessel_cache is None:
        raise RuntimeError("Ingest state not initialized")
    return _vessel_cache
