# AISStream Integration Skills

This document defines implementation rules for working with the AISStream API in this repository. Follow these rules to keep the service compliant, stable, and cost/performance efficient.

## 1) Hard Compliance Rules

- Use `wss://stream.aisstream.io/v0/stream` only. Do not use plain websocket or expose the API key over insecure channels.
- Keep the AISStream API key server-side only. Never connect to AISStream directly from browser clients.
- Send a valid subscription message within 3 seconds after websocket connection opens, or AISStream may close the socket.
- Always include `APIKey` and `BoundingBoxes` in the subscription.
- Keep `FiltersShipMMSI` at 50 entries max.
- Do not send duplicate values inside `FilterMessageTypes` (AISStream can reject this with an error message).
- Treat the API as beta/unstable: message models and behavior can change; do not assume strict schema permanence.

## 2) Subscription Contract (What To Send)

Expected payload shape:

```json
{
  "APIKey": "<server-side key>",
  "BoundingBoxes": [[[-90, -180], [90, 180]]],
  "FiltersShipMMSI": ["368207620"],
  "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
}
```

Rules:

- `BoundingBoxes` format is `[[[lat1, lon1], [lat2, lon2]]]`.
- Corner order does not matter.
- Subscription updates are swap/replace, not merge.
- Throttle subscription updates to at most 1 update per second.

## 3) Runtime Reliability Skills

- **Fast read path first:** always drain websocket quickly, then parse/process asynchronously (already implemented with `self._msg_queue` in `worker/main.py`).
- **Bound memory intentionally:** queue is bounded; track dropped messages and alert if non-zero for sustained periods.
- **Reconnect with backoff:** on transient failures use exponential backoff; on auth/subscription errors back off longer and require operator action.
- **Detect stream-level errors:** parse `{"error": "..."}` server messages and surface explicit operational logs.
- **Keepalive settings:** keep websocket ping/pong enabled with realistic timeouts.

## 4) Throughput and Performance Skills

- AISStream can deliver high volume (docs reference ~300 msgs/sec for global feed). Optimize for sustained ingest, not burst-only.
- Scope bounding boxes as tightly as practical; reducing geographic scope is the highest leverage performance control.
- Filter message types to only what downstream logic needs.
- Batch DB writes and keep flush intervals short to reduce memory pressure and transaction overhead.
- Avoid heavy per-message CPU work on the receive loop; do enrichment in worker tasks, not inline in websocket iteration.

## 5) Data Validation and Parsing Skills

- Validate latitude/longitude ranges (`lat` in `[-90, 90]`, `lon` in `[-180, 180]`).
- Fail soft on malformed records: increment discard/error counters, keep stream alive.
- Parse timestamps defensively (fallback to server current UTC on invalid format).
- Treat message type polymorphism as normal (`PositionReport`, `StandardClassBPositionReport`, `ExtendedClassBPositionReport`, etc.).
- Use `MessageType` + nested `Message[MessageType]` convention when extracting payloads.

## 6) Security and Key Management Skills

- Store `AISSTREAM_API_KEY` in environment/secrets manager, never in source control.
- Rotate keys using AISStream key management when exposure is suspected.
- Never forward raw API key to frontend or logs.
- Redact key-like values in debug dumps and structured logs.

## 7) Operational SLO and Monitoring Skills

Track and alert on:

- websocket connection state (`streaming`, reconnecting, auth/subscription failure)
- input volume (`received`)
- persistence volume (`stored`, vessel upserts)
- quality signals (`discarded`, `errors`)
- backpressure signal (`dropped`) - should be near zero under normal load

Health practices:

- Keep `/health/live` and `/health/ready` tied to DB/Redis dependencies.
- Use `/api/v1/stats` as primary operator dashboard for ingestion health.

## 8) Compatibility Skills (Beta API)

- Pin parser logic to known message families but tolerate unknown fields.
- Default unknown `MessageType` to no-op + metric increment, not hard failure.
- Keep transformation code isolated so schema updates are localized.
- Review AISStream docs periodically and after any unexplained ingest anomaly.

## 9) Repo-Specific Implementation Guidance

For this codebase:

- Keep websocket ingestion in `worker/main.py` backend-only.
- Continue sending subscription immediately after `websockets.connect(...)`.
- Preserve queue-based decoupling between read loop and parsing/writes.
- Keep bounding box values configurable via `.env` (`ZONE_*`) and avoid global feed in constrained environments.
- Continue persisting both dynamic positions and static vessel data with batched writes.

## 10) Pre-Release Compliance Checklist

- API key present and valid in runtime environment.
- Subscription sent within 3 seconds on fresh connection.
- Subscription payload includes required fields and no duplicate filter message types.
- No browser-direct AISStream connection paths exist.
- Backoff/retry behavior verified for network failure and invalid-key scenarios.
- Metrics/stats include dropped/error/discarded counters and are observable.
- Ingest still operates if unknown message fields appear.
