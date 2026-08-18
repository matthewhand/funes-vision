#!/bin/bash
# Capture user-guide screenshots from the synthetic fixture gallery.
# Never points at live camera roots.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export SCREENSHOT_ROOT="${SCREENSHOT_ROOT:-$ROOT/tools/screenshots/fixtures/gallery}"
export SCREENSHOT_INDEX="${SCREENSHOT_INDEX:-$ROOT/index.html}"
export SCREENSHOT_API="${SCREENSHOT_API:-stub}"
export SCREENSHOT_PORT="${SCREENSHOT_PORT:-8899}"
export SHOTS_URL="${SHOTS_URL:-http://127.0.0.1:${SCREENSHOT_PORT}/}"
export SHOTS_OUT="${SHOTS_OUT:-/tmp/webcam_shots}"

if [[ "$SCREENSHOT_ROOT" == *"/mnt/models/Webcam"* ]]; then
  echo "REFUSING live camera root: $SCREENSHOT_ROOT" >&2
  exit 2
fi

mkdir -p "$SHOTS_OUT"
python3 "$ROOT/tools/screenshots/proxy.py" &
PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
sleep 0.4
if ! curl -sf "http://127.0.0.1:${SCREENSHOT_PORT}/images.json" >/dev/null; then
  echo "proxy did not come up on :${SCREENSHOT_PORT}" >&2
  exit 1
fi

if [[ ! -d /tmp/pw/node_modules/playwright ]]; then
  echo "Playwright not installed in /tmp/pw — see tools/screenshots/README.md" >&2
  exit 1
fi
export NODE_PATH=/tmp/pw/node_modules
cd /tmp/pw
node "$ROOT/tools/screenshots/shots.js"
echo "shots in $SHOTS_OUT"
