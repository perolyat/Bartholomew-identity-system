
#!/usr/bin/env bash
set -euo pipefail

base="${1:-http://localhost:5173}"

echo "GET /api/health"
curl -sS "${base}/api/health" | jq . || true

echo
echo "POST /api/chat"
curl -sS -X POST "${base}/api/chat" -H 'Content-Type: application/json' -d '{"message":"Hello Bartholomew"}' | jq . || true

echo
echo "POST /api/water/log (250)"
curl -sS -X POST "${base}/api/water/log" -H 'Content-Type: application/json' -d '{"ml":250}' | jq . || true

echo
echo "GET /api/water/today"
curl -sS "${base}/api/water/today" | jq . || true
