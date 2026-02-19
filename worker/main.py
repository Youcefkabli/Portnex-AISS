"""
AIS ingestion worker (Phase 1).

- Connects to AISstream, parses and filters by bbox.
- Writes to TimescaleDB (positions_1sec, vessels), runs flush_1min_from_agg periodically.
- Publishes each position to Redis for live stream; writes stats to Redis.
"""
import asyncio
import json
import logging
import os
import sys
import time

# Ensure project root is on path when running as worker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from typing import Any

import websockets
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models import Vessel
from app.services.redis_client import publish_live_position, write_stats

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
    Normalise AIS ETA payload to DB text.
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
        # AIS often uses zeroed/unknown ETA fields.
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
    if not (
        settings.ZONE_LAT_MIN <= lat <= settings.ZONE_LAT_MAX
        and settings.ZONE_LON_MIN <= lon <= settings.ZONE_LON_MAX
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
        # positions_1sec.msg_type is numeric in DB schema; use AIS MessageID.
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
    """
    AISStream docs define server-side failures as: {"error": "..."}.
    Parse and surface these explicitly (e.g., invalid API key/filter type).
    """
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


async def _flush_1sec(batch: list[dict]) -> None:
    if not batch:
        return
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("""
            INSERT INTO positions_1sec
                (time, mmsi, latitude, longitude, speed, course,
                 heading, nav_status, rot, msg_type)
            VALUES
                (:time, :mmsi, :latitude, :longitude, :speed, :course,
                 :heading, :nav_status, :rot, :msg_type)
            ON CONFLICT DO NOTHING
        """),
            batch,
        )
        await s.commit()
    logger.debug("1sec flush: %d rows", len(batch))


async def _flush_vessels(batch: list[dict]) -> None:
    if not batch:
        return
    async with AsyncSessionLocal() as s:
        stmt = pg_insert(Vessel).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["mmsi"],
            set_={
                k: getattr(stmt.excluded, k)
                for k in (
                    "imo",
                    "callsign",
                    "name",
                    "ship_type",
                    "ship_type_text",
                    "dim_to_bow",
                    "dim_to_stern",
                    "dim_to_port",
                    "dim_to_starboard",
                    "draught",
                    "destination",
                    "eta",
                    "last_updated",
                )
            },
        )
        await s.execute(stmt)
        await s.commit()


async def _materialise_1min() -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(text("SELECT flush_1min_from_agg()"))
        await s.commit()


class IngestionWorker:
    def __init__(self):
        self._running = False
        self._pos_batch: list[dict] = []
        self._vessel_batch: list[dict] = []
        self.stats = {
            "status": "stopped",
            "received": 0,
            "stored": 0,
            "discarded": 0,
            "vessels": 0,
            "errors": 0,
            "dropped": 0,
            "queue_depth_hwm": 0,
            "publish_queue_hwm": 0,
            "last_flush_count": 0,
            "last_flush_duration_ms": 0,
        }
        self._msg_queue: asyncio.Queue[str | bytes | None] = asyncio.Queue(
            maxsize=settings.WORKER_MSG_QUEUE_SIZE
        )
        self._publish_queue: asyncio.Queue[dict | None] = asyncio.Queue(
            maxsize=settings.WORKER_PUBLISH_QUEUE_SIZE
        )
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
        self._spawn(self._flush_loop(), "ais-flush")
        self._spawn(self._publish_loop(), "ais-publish")
        self._spawn(self._materialise_loop(), "ais-1min")
        self._spawn(self._stats_loop(), "ais-stats")
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
        try:
            self._publish_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._flush_all()
        self.stats["status"] = "stopped"
        logger.info("AIS worker stopped")

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(settings.BATCH_TIMEOUT_SEC)
            try:
                t0 = time.perf_counter()
                await self._flush_all()
                self.stats["last_flush_duration_ms"] = round(
                    (time.perf_counter() - t0) * 1000
                )
            except Exception as exc:
                self.stats["errors"] += 1
                logger.warning("flush error: %s", exc)

    async def _publish_loop(self) -> None:
        while self._running:
            try:
                row = await asyncio.wait_for(
                    self._publish_queue.get(), timeout=1.0
                )
                if row is None:
                    return
                await publish_live_position(row)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.stats["errors"] += 1
                logger.debug("publish error: %s", exc)

    async def _materialise_loop(self) -> None:
        while self._running:
            await asyncio.sleep(60)
            try:
                await _materialise_1min()
            except Exception as exc:
                logger.warning("1min materialise error: %s", exc)

    async def _stats_loop(self) -> None:
        while self._running:
            await asyncio.sleep(5)
            try:
                await write_stats(self.stats)
            except Exception as exc:
                logger.debug("stats write error: %s", exc)

    async def _connect_loop(self) -> None:
        delay = 1
        while self._running:
            try:
                await self._stream()
                delay = 1
            except AISStreamSubscriptionError as exc:
                self.stats["errors"] += 1
                self.stats["status"] = f"stream error ({exc})"
                logger.error(
                    "AISStream subscription/authentication failed: %s. "
                    "Check AISSTREAM_API_KEY and FilterMessageTypes.",
                    exc,
                )
                # Avoid aggressive reconnect loops for invalid credentials.
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.stats["errors"] += 1
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
                        self.stats["errors"] += 1
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
                        self.stats["dropped"] += 1
            finally:
                try:
                    self._msg_queue.put_nowait(None)
                except asyncio.QueueFull:
                    # Ensure consumer can exit even under sustained high load.
                    await self._msg_queue.put(None)
                await consumer

    def _handle(self, raw: str | bytes) -> None:
        msg = json.loads(raw)
        msg_class = msg.get("MessageType", "")
        self.stats["received"] += 1
        if "Position" in msg_class or msg_class in (
            "PositionReport",
            "StandardClassBPositionReport",
            "ExtendedClassBPositionReport",
        ):
            row = _parse_position(msg)
            if row is None:
                self.stats["discarded"] += 1
                return
            self._pos_batch.append(row)
            self.stats["stored"] += 1
            try:
                self._publish_queue.put_nowait(row)
                self.stats["publish_queue_hwm"] = max(
                    self.stats.get("publish_queue_hwm", 0),
                    self._publish_queue.qsize(),
                )
            except asyncio.QueueFull:
                self.stats["dropped"] += 1
        elif "Static" in msg_class or msg_class in (
            "ShipStaticData",
        ):
            row = _parse_static(msg)
            if row:
                self._vessel_batch.append(row)
                self.stats["vessels"] += 1

    async def _flush_all(self) -> None:
        n_pos = len(self._pos_batch)
        n_vessel = len(self._vessel_batch)
        await self._flush_pos_now()
        await self._flush_vessels_now()
        self.stats["last_flush_count"] = n_pos + n_vessel

    async def _flush_pos_now(self) -> None:
        batch, self._pos_batch = self._pos_batch, []
        await _flush_1sec(batch)

    async def _flush_vessels_now(self) -> None:
        batch, self._vessel_batch = self._vessel_batch, []
        await _flush_vessels(batch)


async def run_worker() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    worker = IngestionWorker()
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
