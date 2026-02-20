"""
AIS ingestion (in-process).

- Connects to AISstream, parses and filters by bbox.
- Broadcasts each position in-process (no Redis); updates shared stats and vessel cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

# Ensure project root is on path when running as worker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from typing import Any, Callable, Dict

import websockets

from app.core.config import settings

logger = logging.getLogger("ais.worker")


class AISStreamSubscriptionError(Exception):
    """Raised when AISStream returns a subscription/authentication error."""


# ─────────────────────────────────────────────────────────────
# Ship type helper
# ─────────────────────────────────────────────────────────────
SHIP_TYPES: dict[int, str] = {
    0: "Not available",
    30: "Fishing",
    31: "Towing",
    33: "Dredger",
    35: "Military",
    36: "Sailing",
    37: "Pleasure craft",
    40: "High-speed craft",
    50: "Pilot vessel",
    51: "SAR vessel",
    52: "Tug",
    60: "Passenger",
    70: "Cargo",
    80: "Tanker",
    90: "Other",
}


def _ship_type_text(code: int | None) -> str | None:
    if code is None:
        return None
    for base, label in sorted(SHIP_TYPES.items(), reverse=True):
        if code >= base:
            return label
    return "Unknown"


def _ts(raw: str | None) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _eta_to_text(raw: Any) -> str | None:
    """
    Normalise AIS ETA payload to text.
    AISStream may provide ETA as object: {"Month":..,"Day":..,"Hour":..,"Minute":..}
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        value = raw.strip()
        return value or None
    if isinstance(raw, dict):
        month = int(raw.get("Month", 0) or 0)
        day = int(raw.get("Day", 0) or 0)
        hour = int(raw.get("Hour", 0) or 0)
        minute = int(raw.get("Minute", 0) or 0)
        if month == 0 and day == 0:
            return None
        return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
    return None


def _parse_position(msg: dict[str, Any]) -> dict | None:
    meta = msg.get("MetaData", {})
    payload = msg.get("Message", {})
    pos = (
        payload.get("PositionReport")
        or payload.get("StandardClassBPositionReport")
        or payload.get("ExtendedClassBPositionReport")
    )
    if not pos:
        return None
    mmsi = int(meta.get("MMSI", 0) or pos.get("UserID", 0))
    if not mmsi:
        return None
    lat = pos.get("Latitude")
    lon = pos.get("Longitude")
    if lat is not None and abs(lat) > 90:
        lat = None
    if lon is not None and abs(lon) > 180:
        lon = None
    if lat is None or lon is None:
        return None
    lon_west = min(settings.ZONE_LON_MIN, settings.ZONE_LON_MAX)
    lon_east = max(settings.ZONE_LON_MIN, settings.ZONE_LON_MAX)
    if not (
        settings.ZONE_LAT_MIN <= lat <= settings.ZONE_LAT_MAX
        and lon_west <= lon <= lon_east
    ):
        return None
    return {
        "time": _ts(meta.get("time_utc")),
        "mmsi": mmsi,
        "latitude": lat,
        "longitude": lon,
        "speed": pos.get("Sog"),
        "course": pos.get("Cog"),
        "heading": pos.get("TrueHeading"),
        "nav_status": pos.get("NavigationalStatus"),
        "rot": pos.get("RateOfTurn"),
        "msg_type": pos.get("MessageID"),
    }


def _parse_static(msg: dict[str, Any]) -> dict | None:
    meta = msg.get("MetaData", {})
    payload = msg.get("Message", {})
    static = payload.get("ShipStaticData") or payload.get("ClassBCsStaticDataReport")
    if not static:
        return None
    mmsi = int(meta.get("MMSI", 0) or static.get("UserID", 0))
    if not mmsi:
        return None
    dim = static.get("Dimension", {}) or {}
    code = static.get("Type")
    return {
        "mmsi": mmsi,
        "imo": static.get("ImoNumber"),
        "callsign": (static.get("CallSign") or "").strip() or None,
        "name": (static.get("Name") or "").strip() or None,
        "ship_type": code,
        "ship_type_text": _ship_type_text(code),
        "dim_to_bow": dim.get("A"),
        "dim_to_stern": dim.get("B"),
        "dim_to_port": dim.get("C"),
        "dim_to_starboard": dim.get("D"),
        "draught": static.get("MaximumStaticDraught"),
        "destination": (static.get("Destination") or "").strip() or None,
        "eta": _eta_to_text(static.get("Eta")),
        "last_updated": _ts(meta.get("time_utc")),
    }


def _extract_stream_error(raw: str | bytes) -> str | None:
    try:
        msg = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(msg, dict):
        return None
    err = msg.get("error") or msg.get("Error")
    if isinstance(err, str):
        err = err.strip()
        return err or None
    return None


def _serialize_position(row: dict[str, Any]) -> str:
    """JSON-serialise position; datetime -> ISO string."""
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return json.dumps(out)


# Default queue size for AIS message backpressure
WORKER_MSG_QUEUE_SIZE = 50_000


class IngestionWorker:
    """
    In-process AIS ingest: AISStream -> parse -> broadcast + update stats/vessel_cache.
    """

    def __init__(
        self,
        *,
        broadcast_callback: Callable[[str], None],
        stats: Dict[str, Any],
        vessel_cache: Dict[int, dict],
        msg_queue_size: int = WORKER_MSG_QUEUE_SIZE,
    ):
        self._broadcast = broadcast_callback
        self.stats = stats
        self._vessel_cache = vessel_cache
        self._running = False
        self._msg_queue: asyncio.Queue[str | bytes | None] = asyncio.Queue(maxsize=msg_queue_size)
        self._tasks: set[asyncio.Task[Any]] = set()

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start(self) -> None:
        if not settings.AISSTREAM_API_KEY.strip():
            self.stats["status"] = "auth error (missing AISSTREAM_API_KEY)"
            logger.error(
                "AISSTREAM_API_KEY is empty; set a valid key from https://aisstream.io/apikeys"
            )
            return
        self._running = True
        self._spawn(self._connect_loop(), "ais-ws")
        logger.info(
            "AIS worker started — zone: %s [%.4f,%.4f / %.4f,%.4f]",
            settings.ZONE_NAME,
            settings.ZONE_LAT_MIN,
            settings.ZONE_LAT_MAX,
            settings.ZONE_LON_MIN,
            settings.ZONE_LON_MAX,
        )

    async def stop(self) -> None:
        self._running = False
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        try:
            self._msg_queue.put_nowait(None)
        except asyncio.QueueFull:
            await self._msg_queue.put(None)
        self.stats["status"] = "stopped"
        logger.info("AIS worker stopped")

    async def _connect_loop(self) -> None:
        delay = 1
        while self._running:
            try:
                await self._stream()
                delay = 1
            except AISStreamSubscriptionError as exc:
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                self.stats["status"] = f"stream error ({exc})"
                logger.error(
                    "AISStream subscription/authentication failed: %s. "
                    "Check AISSTREAM_API_KEY and FilterMessageTypes.",
                    exc,
                )
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                self.stats["status"] = f"reconnecting ({exc})"
                logger.warning("WS error: %s — retry in %ds", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _stream(self) -> None:
        sub = {
            "APIKey": settings.AISSTREAM_API_KEY.strip(),
            "BoundingBoxes": settings.bounding_box(),
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
                "ShipStaticData",
            ],
        }
        async with websockets.connect(
            settings.AISSTREAM_WS_URL, ping_interval=20, ping_timeout=30
        ) as ws:
            await ws.send(json.dumps(sub))
            self.stats["status"] = "streaming"
            logger.info("AISstream connected — zone: %s", settings.ZONE_NAME)

            async def consume():
                while self._running:
                    raw = await self._msg_queue.get()
                    if raw is None:
                        return
                    try:
                        self._handle(raw)
                    except Exception as exc:
                        self.stats["errors"] = self.stats.get("errors", 0) + 1
                        logger.debug("Parse error: %s", exc)

            consumer = asyncio.create_task(consume())
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    stream_error = _extract_stream_error(raw)
                    if stream_error:
                        raise AISStreamSubscriptionError(stream_error)
                    try:
                        self._msg_queue.put_nowait(raw)
                        self.stats["queue_depth_hwm"] = max(
                            self.stats.get("queue_depth_hwm", 0),
                            self._msg_queue.qsize(),
                        )
                    except asyncio.QueueFull:
                        self.stats["dropped"] = self.stats.get("dropped", 0) + 1
            finally:
                try:
                    self._msg_queue.put_nowait(None)
                except asyncio.QueueFull:
                    await self._msg_queue.put(None)
                await consumer

    def _handle(self, raw: str | bytes) -> None:
        msg = json.loads(raw)
        msg_class = msg.get("MessageType", "")
        self.stats["received"] = self.stats.get("received", 0) + 1
        if "Position" in msg_class or msg_class in (
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport",
        ):
            row = _parse_position(msg)
            if row is None:
                self.stats["discarded"] = self.stats.get("discarded", 0) + 1
                return
            self.stats["stored"] = self.stats.get("stored", 0) + 1
            payload = _serialize_position(row)
            self._broadcast(payload)
        elif "Static" in msg_class or msg_class in (
            "ShipStaticData",
        ):
            row = _parse_static(msg)
            if row:
                mmsi = row["mmsi"]
                self._vessel_cache[mmsi] = row
                self.stats["vessels"] = self.stats.get("vessels", 0) + 1


async def run_worker_standalone() -> None:
    """Standalone entry (no API): uses no-op broadcast and local stats/cache."""
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
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

    def noop_broadcast(_: str) -> None:
        pass

    worker = IngestionWorker(
        broadcast_callback=noop_broadcast,
        stats=stats,
        vessel_cache=vessel_cache,
    )
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()


def main() -> None:
    asyncio.run(run_worker_standalone())


if __name__ == "__main__":
    main()
