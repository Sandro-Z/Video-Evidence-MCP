#!/bin/sh
set -eu

: "${TUNNEL_ID:?Set TUNNEL_ID in the environment}"
: "${CONTROL_PLANE_API_KEY:?Set CONTROL_PLANE_API_KEY in the environment}"

tunnel-client init \
  --profile video-evidence \
  --tunnel-id "$TUNNEL_ID" \
  --mcp-server-url http://127.0.0.1:8787/mcp
tunnel-client doctor --profile video-evidence --explain
