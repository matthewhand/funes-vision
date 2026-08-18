#!/bin/bash

IMAGE_DIR=$1
# Global lock: analyze_images.py scans ALL camera dirs and writes shared
# analysis.json, so concurrent instances must never run it in parallel.
LOCKFILE="/tmp/webcam_analysis.lock"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Single execution gate with 1-hour timeout
run_analysis() {
  (
    # Wait up to 3600 seconds (1 hour) for the lock
    if flock -x -w 3600 200; then
        # Debounce: waiters queued during a long sweep stampede when it
        # ends - skip if another run completed moments ago.
        MARKER="/tmp/webcam_analysis.lastrun"
        if [ -f "$MARKER" ] && [ $(( $(date +%s) - $(stat -c %Y "$MARKER") )) -lt 45 ]; then
            exit 0
        fi
        echo "$(date): Starting gated analysis for $IMAGE_DIR..."
        
        # 1. Update images.json
        mapfile -t images < <(ls -t "$IMAGE_DIR" 2>/dev/null | grep -E '\.(jpg|jpeg|png|gif)$')
        if [ ${#images[@]} -eq 0 ] || [ -z "${images[0]}" ]; then
            echo "[]" > "$IMAGE_DIR/images.json"
        else
            printf '%s\n' "${images[@]}" | jq -R . | jq -s . > "$IMAGE_DIR/images.json"
        fi

        # 2. Run Python analysis (Fast Pass + Deep Pass)
        if [ -f "$HOME/.litellm/.env" ]; then
            set -a
            source "$HOME/.litellm/.env"
            set +a
        fi
        export OPENROUTER_API_KEY
        
        # We run the analysis script. Retention runs first inside it; a
        # non-zero exit must not be reported as a successful sweep.
        analysis_rc=0
        python3 "$BASE_DIR/analyze_images.py" || analysis_rc=$?

        # 3. Sync template and results to web root (even after a partial run
        # so a recovered analysis.json still propagates to nginx roots).
        cp "$BASE_DIR/index.html" "$IMAGE_DIR/index.html" 2>/dev/null || true
        # PWA static assets (installable; no service worker)
        [ -f "$BASE_DIR/manifest.json" ] && cp "$BASE_DIR/manifest.json" "$IMAGE_DIR/manifest.json" 2>/dev/null || true
        [ -f "$BASE_DIR/icon.svg" ] && cp "$BASE_DIR/icon.svg" "$IMAGE_DIR/icon.svg" 2>/dev/null || true
        [ -f "$BASE_DIR/favicon.ico" ] && cp "$BASE_DIR/favicon.ico" "$IMAGE_DIR/favicon.ico" 2>/dev/null || true
        [ -f "$BASE_DIR/lucide.min.js" ] && cp "$BASE_DIR/lucide.min.js" "$IMAGE_DIR/lucide.min.js" 2>/dev/null || true
        [ -f "$BASE_DIR/analysis.json" ] && cp "$BASE_DIR/analysis.json" "$IMAGE_DIR/analysis.json" 2>/dev/null || true
        [ -f "$BASE_DIR/bursts.json" ] && cp "$BASE_DIR/bursts.json" "$IMAGE_DIR/bursts.json" 2>/dev/null || true
        [ -f "$BASE_DIR/pins.json" ] && cp "$BASE_DIR/pins.json" "$IMAGE_DIR/pins.json" 2>/dev/null || true

        if [ "$analysis_rc" -eq 0 ]; then
            touch "$MARKER"
            echo "$(date): Analysis and sync complete."
        else
            echo "$(date): Analysis FAILED (exit $analysis_rc); web root sync attempted, lastrun not advanced."
        fi
    fi
  ) 200>$LOCKFILE
}

# Background loop for "Idle Catch-up"; interval is read each cycle so
# settings.json changes apply without restarts
idle_sweep() {
    while true; do
        # Attempt a run. If lock is busy, run_analysis will wait.
        run_analysis
        INTERVAL=$(jq -r '.idle_sweep_seconds // 60' "$BASE_DIR/settings.json" 2>/dev/null || echo 60)
        sleep "${INTERVAL:-60}"
    done
}

# Start the idle sweep in background
idle_sweep &

# Initial run on startup
run_analysis &

# Watch for new image creations
nice -n 10 inotifywait -m -e create --format '%w%f' "$IMAGE_DIR" | while read new_image
do
    if [[ $new_image =~ \.(jpg|jpeg|png|gif)$ ]]; then
        echo "New image detected: $new_image. Triggering analysis..."
        run_analysis &
        sleep 5
    fi
done
