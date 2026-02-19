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
        "msg_type": msg.get("MessageType"),
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
        "eta": static.get("Eta"),
        "last_updated": _ts(meta.get("time_utc")),
    }


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
        }

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._flush_loop(), name="ais-flush")
        asyncio.create_task(self._materialise_loop(), name="ais-1min")
        asyncio.create_task(self._stats_loop(), name="ais-stats")
        asyncio.create_task(self._connect_loop(), name="ais-ws")
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
        await self._flush_all()
        self.stats["status"] = "stopped"
        logger.info("AIS worker stopped")

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(settings.BATCH_TIMEOUT_SEC)
            await self._flush_all()

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
            "APIKey": settings.AISSTREAM_API_KEY,
            "BoundingBoxes": settings.bounding_box(),
            "FilterMessageTypes": [
                "PositionReport",
                "StandardClassBPositionReport",
                "ExtendedClassBPositionReport",
                "ShipStaticData",
                "ClassBCsStaticDataReport",
            ],
        }
        async with websockets.connect(
            settings.AISSTREAM_WS_URL, ping_interval=20, ping_timeout=30
        ) as ws:
            await ws.send(json.dumps(sub))
            self.stats["status"] = "streaming"
            logger.info("AISstream connected — zone: %s", settings.ZONE_NAME)
            async for raw in ws:
                if not self._running:
                    break
                try:
                    self._handle(raw)
                except Exception as exc:
                    self.stats["errors"] += 1
                    logger.debug("Parse error: %s", exc)

    def _handle(self, raw: str) -> None:
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
            asyncio.create_task(self._publish_and_maybe_flush(row))
        elif "Static" in msg_class or msg_class in (
            "ShipStaticData",
            "ClassBCsStaticDataReport",
        ):
            row = _parse_static(msg)
            if row:
                self._vessel_batch.append(row)
                self.stats["vessels"] += 1
                if len(self._vessel_batch) >= settings.BATCH_SIZE:
                    asyncio.create_task(self._flush_vessels_now())

    async def _publish_and_maybe_flush(self, row: dict) -> None:
        await publish_live_position(row)
        if len(self._pos_batch) >= settings.BATCH_SIZE:
            await self._flush_pos_now()

    async def _flush_all(self) -> None:
        await self._flush_pos_now()
        await self._flush_vessels_now()

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
