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
        
        # We run the analysis script
        python3 "$BASE_DIR/analyze_images.py"
        
        # 3. Sync template and results to web root
        cp "$BASE_DIR/index.html" "$IMAGE_DIR/index.html"
        [ -f "$BASE_DIR/analysis.json" ] && cp "$BASE_DIR/analysis.json" "$IMAGE_DIR/analysis.json"
        [ -f "$BASE_DIR/bursts.json" ] && cp "$BASE_DIR/bursts.json" "$IMAGE_DIR/bursts.json"
        [ -f "$BASE_DIR/pins.json" ] && cp "$BASE_DIR/pins.json" "$IMAGE_DIR/pins.json"

        touch "$MARKER"
        echo "$(date): Analysis and sync complete."
    fi
  ) 200>$LOCKFILE
}

# Background loop for "Idle Catch-up"
idle_sweep() {
    while true; do
        # Attempt a run. If lock is busy, run_analysis will wait.
        run_analysis
        # Sleep for a bit to allow other processes a chance at the lock
        sleep 60
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
