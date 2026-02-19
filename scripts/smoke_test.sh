#!/usr/bin/env bash
# Phase 1 smoke test. Run after: docker compose up -d
# Requires: curl
set -e
BASE="${BASE_URL:-http://localhost:8000}"

echo "=== Health liveness ==="
curl -sf "$BASE/health/live" | head -1

echo "=== Health readiness ==="
curl -sf "$BASE/health/ready" | head -1

echo "=== Stats ==="
curl -sf "$BASE/api/v1/stats" | head -1

echo "=== Live stream (3s sample) ==="
timeout 3 curl -sN "$BASE/api/v1/live" || true

echo "=== Smoke test done ==="
