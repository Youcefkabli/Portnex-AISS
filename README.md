# AIS Phase 1 — Real-time vessel stream

Live AIS vessel positions in a fixed area: **single process** — API runs in-process AISstream ingestion and fans out via in-memory broadcaster (no Redis, no database). Minimal latency from AIS message to client.

## AIS integration guide

For implementation/compliance guardrails specific to AISStream, see `.cursor/rules/aisstream-compliance.mdc`.

## Quick start (Docker)

1. Copy env and set your AISstream API key:
   ```bash
   cp .env.example .env
   # Edit .env: set AISSTREAM_API_KEY
   ```

2. Start the API (ingest runs inside the same container):
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
| `GET /health/ready` | Readiness (no external deps) |
| `GET /api/v1/live` | SSE stream of current vessel positions |
| `GET /api/v1/stats` | Worker stats + zone bbox |
| `GET /api/v1/vessels/{mmsi}` | Static vessel details from stream cache (name, type, destination, etc.) |

**Note:** `/api/v1/positions/recent` is removed (no database). Vessel data is in-memory only, from the live stream.

## Connection stability (AISstream)

AISstream closes the connection if the client does not read data fast enough. The ingest task drains the WebSocket into an in-memory queue and broadcasts positions in-process so the connection stays healthy. If you subscribe to a very large area (e.g. the whole world), use a machine with enough CPU; otherwise use a smaller bounding box in `.env` to reduce message volume.

**Stats:** `GET /api/v1/stats` returns ingest counters (`received`, `stored`, `dropped`, `errors`), `queue_depth_hwm`, and `sse_dropped` (SSE clients that were too slow). Keep `dropped` and `sse_dropped` near zero under normal load.

## Local run (no Docker)

- No Postgres or Redis required.
- Set `AISSTREAM_API_KEY` (and optional zone/LOG_LEVEL) in `.env`.
- Run: `uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000`

Ingest starts automatically with the API.

## Validation

- **Functional:** After starting, confirm `status` in `/api/v1/stats` is `streaming`; positions and vessels should flow to the live SSE. Vessel details appear as static data is received from AISstream.
- **Backpressure:** Under sustained load, check `queue_depth_hwm` in stats; if it approaches the internal queue size, consider a smaller bbox or higher resources. Alert if `dropped` or `sse_dropped` grows.

## Project layout (Phase 1)

```
app/
  core/config.py      # Settings (bbox, AISstream, API)
  db/                 # Schemas (API responses); database stubbed
  api/                # FastAPI app, health, router
  services/           # In-memory live broadcaster, ingest state
worker/
  main.py             # AISstream ingestion (used in-process by API)
```
Run with: `uvicorn app.api.main:app --host 0.0.0.0 --port 8000`
