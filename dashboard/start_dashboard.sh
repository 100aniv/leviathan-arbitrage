#!/bin/bash
# Dashboard startup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export NEXT_TELEMETRY_DISABLED=1
export NEXT_PUBLIC_ENGINE_URL=http://localhost:8000

cd "$SCRIPT_DIR"
node node_modules/.bin/next dev --port 3000
