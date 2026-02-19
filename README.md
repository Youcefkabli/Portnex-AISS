# AIS Phase 1 — Real-time vessel stream

Live AIS vessel positions in a fixed area: **worker** ingests from AISstream and writes to TimescaleDB + Redis; **API** serves REST and SSE from Redis/DB.

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

## Local run (no Docker)

- Start Postgres (with TimescaleDB) and Redis, run `init.sql` on the DB.
- Set `DATABASE_URL` and `REDIS_URL` in `.env`.
- API: `uvicorn main:app --reload --host 0.0.0.0 --port 8000`
- Worker: `python -m worker.main`

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
