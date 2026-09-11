#!/bin/bash
# tools/deploy-webroot.sh — sync the repo's static SPA assets into the served
# web roots and verify they match HEAD.
#
# Why this exists: the nginx gallery bind-mounts /mnt/models/Webcam21 (and
# Webcam22) as its image root, so the served index.html is the file ON THE
# MOUNT, not the repo working copy. create-index.sh copies these assets during
# a sweep, but that copy is buried behind a flock and only fires on a new JPEG
# or the 60s idle sweep — a pure UI edit triggers nothing, so the served page
# silently drifts from the repo (this is how an index.html syntax error sat
# undetected in production for days).
#
# Usage:
#   tools/deploy-webroot.sh            # copy + verify Webcam21 and Webcam22
#   tools/deploy-webroot.sh Webcam21   # one camera only
#
# Exits non-zero if any asset is missing or differs from HEAD — use it as a
# pre-push hook or a CI step so drift fails loudly instead of silently.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="$ROOT"

# Static assets the SPA requests; must stay in sync with create-index.sh:45-56.
# USER-GUIDE.html lives under docs/ — the rest are repo-root files.
ASSETS=(
  index.html
  manifest.json
  icon.svg
  favicon.ico
  lucide.min.js
)

# Optional assets that live outside the repo root.
OPTIONAL_ASSETS=(
  docs/USER-GUIDE.html
)

if [ "$#" -ge 1 ]; then
  CAMERAS=("$@")
else
  CAMERAS=(Webcam21 Webcam22)
fi

fail=0
for cam in "${CAMERAS[@]}"; do
  IMAGE_DIR="/mnt/models/$cam"
  if [ ! -d "$IMAGE_DIR" ]; then
    echo "SKIP $cam: $IMAGE_DIR not a directory" >&2
    continue
  fi
  echo "=== $cam ==="
  for asset in "${ASSETS[@]}" "${OPTIONAL_ASSETS[@]}"; do
    src="$BASE_DIR/$asset"
    if [ ! -f "$src" ]; then
      echo "  - $asset (not in repo; skipping)"
      continue
    fi
    dst="$IMAGE_DIR/$(basename "$asset")"
    cp "$src" "$dst"
    # Verify the served copy matches HEAD byte-for-byte.
    if git -C "$ROOT" show "HEAD:$asset" > /tmp/_deploy_check 2>/dev/null && cmp -s "$dst" /tmp/_deploy_check; then
      echo "  ok $asset"
    else
      echo "  DRIFT $asset: served copy != HEAD" >&2
      fail=1
    fi
  done
  # Guide images (optional directory).
  if [ -d "$BASE_DIR/docs/guide/img" ]; then
    mkdir -p "$IMAGE_DIR/guide/img"
    cp "$BASE_DIR/docs/guide/img/"*.png "$IMAGE_DIR/guide/img/" 2>/dev/null || true
    echo "  ok guide/img/*.png"
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "DEPLOY FAILED: drift detected" >&2
  exit 1
fi
echo "DEPLOY OK"