# AIS Phase 1 — Real-time vessel stream

Live AIS vessel positions in a fixed area: **worker** ingests from AISstream and writes to TimescaleDB + Redis; **API** serves REST and SSE from Redis/DB.

## AIS integration guide

For implementation/compliance guardrails specific to AISStream, see `skills.md`.

## Quick start (Docker)

1. Copy env and set your AISstream API key:
   ```bash
   cp .env.example .env
   # Edit .env: set AISSTREAM_API_KEY
   ```

2. Start the stack:
   ```bash
   docker compose up -d
   ```

3. Check health and stream:
   - Liveness: `curl http://localhost:8000/health/live`
   - Readiness: `curl http://localhost:8000/health/ready`
   - Stats: `curl http://localhost:8000/api/v1/stats`
   - Live SSE: `curl -N http://localhost:8000/api/v1/live`
   - Live table page: open `http://localhost:8000/` in your browser
   - Live map page: open `http://localhost:8000/map` in your browser

4. Optional smoke script (after stack is up):
   ```bash
   chmod +x scripts/smoke_test.sh && ./scripts/smoke_test.sh
   ```

## Phase 1 API

| Endpoint | Description |
|----------|-------------|
| `GET /health/live` | Liveness probe |
| `GET /health/ready` | Readiness (DB + Redis) |
| `GET /api/v1/live` | SSE stream of current vessel positions |
| `GET /api/v1/stats` | Worker stats + zone bbox |
| `GET /api/v1/positions/recent?minutes=15` | Recent positions (debug) |
| `GET /api/v1/vessels/{mmsi}` | Static vessel details (name, type, destination, etc.) |

## Connection stability (AISstream)

AISstream closes the connection if the client does not read data fast enough. The worker drains the WebSocket into an in-memory queue and processes in separate publisher/flush workers so the connection stays healthy. If you subscribe to a very large area (e.g. the whole world), use a machine with enough CPU; otherwise use a smaller bounding box in `.env` to reduce message volume.

**Stats and backpressure:** `GET /api/v1/stats` returns worker counters (`received`, `stored`, `dropped`, `errors`) plus optional backpressure signals: `queue_depth_hwm`, `publish_queue_hwm`, `last_flush_count`, `last_flush_duration_ms`, and `sse_dropped` (SSE clients that were too slow). Keep `dropped` and `sse_dropped` near zero under normal load.

**Tuning (optional):** In `.env` you can set `BATCH_SIZE`, `BATCH_TIMEOUT_SEC`, `WORKER_MSG_QUEUE_SIZE`, and `WORKER_PUBLISH_QUEUE_SIZE` to tune throughput and memory; see `.env.example` and `app/core/config.py` defaults.

## Local run (no Docker)

- Start Postgres (with TimescaleDB) and Redis, run `init.sql` on the DB.
- Set `DATABASE_URL` and `REDIS_URL` in `.env`.
- API: `uvicorn main:app --reload --host 0.0.0.0 --port 8000`
- Worker: `python -m worker.main`

## Validation (resilience and performance)

- **Functional:** After starting the stack, confirm worker connects and `status` in `/api/v1/stats` is `streaming`; positions and vessels should flow to the live SSE and DB.
- **Backpressure:** Under sustained load, check `queue_depth_hwm` and `publish_queue_hwm` in stats; if they approach queue sizes, consider a smaller bbox or higher resources. Alert if `dropped` or `sse_dropped` grows.
- **Resilience:** Temporarily stop Redis or DB and verify the worker logs errors and reconnects without crashing; flush/ publish loops use bounded error handling and retries.
- **Data integrity:** After worker restarts or reconnects, duplicate positions for the same `(time, mmsi)` are avoided via `ON CONFLICT DO NOTHING` and primary keys on `positions_1sec` and `positions_1min` (see `init.sql`).

## Project layout (Phase 1)

```
app/
  core/config.py      # Settings (bbox, DB, Redis, AISstream)
  db/                 # Models, session, schemas
  api/                # FastAPI app, health, router
  services/           # Redis publish/subscribe
worker/
  main.py             # AISstream ingestion, DB write, Redis publish
main.py               # uvicorn entrypoint
init.sql              # TimescaleDB schema (run once)
```
