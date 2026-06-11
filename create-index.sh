#!/bin/bash

IMAGE_DIR=$1
LOCKFILE="/tmp/webcam_analysis.lock"
BASE_DIR="/home/user/webcam"

# Single execution gate with 1-hour timeout
run_analysis() {
  (
    # Wait up to 3600 seconds (1 hour) for the lock
    if flock -x -w 3600 200; then
        echo "$(date): Starting gated analysis for $IMAGE_DIR..."
        
        # 1. Update images.json
        mapfile -t images < <(ls -t "$IMAGE_DIR" 2>/dev/null | grep -E '\.(jpg|jpeg|png|gif)$')
        if [ ${#images[@]} -eq 0 ] || [ -z "${images[0]}" ]; then
            echo "[]" > "$IMAGE_DIR/images.json"
        else
            printf '%s\n' "${images[@]}" | jq -R . | jq -s . > "$IMAGE_DIR/images.json"
        fi

        # 2. Run Python analysis (Fast Pass + Deep Pass)
        # We source env for credentials
        if [ -f "$HOME/.litellm/.env" ]; then
            set -a
            source "$HOME/.litellm/.env"
            set +a
        fi
        export OPENROUTER_API_KEY
        
        python3 "$BASE_DIR/analyze_images.py"
        
        # 3. Sync template and results to web root
        cp "$BASE_DIR/index.html" "$IMAGE_DIR/index.html"
        [ -f "$BASE_DIR/analysis.json" ] && cp "$BASE_DIR/analysis.json" "$IMAGE_DIR/analysis.json"
        [ -f "$BASE_DIR/bursts.json" ] && cp "$BASE_DIR/bursts.json" "$IMAGE_DIR/bursts.json"

        echo "$(date): Analysis and sync complete."
    else
        echo "$(date): Timeout waiting for lock - another instance might be stuck."
    fi
  ) 200>$LOCKFILE
}

# Initial run on startup
run_analysis &

# Watch for new image creations
nice -n 10 inotifywait -m -e create --format '%w%f' "$IMAGE_DIR" | while read new_image
do
    # Check if the new file is an image
    if [[ $new_image =~ \.(jpg|jpeg|png|gif)$ ]]; then
        echo "New image detected: $new_image. Triggering analysis..."
        run_analysis &
        # Add a small delay to debounce rapid creates
        sleep 5
    fi
done
