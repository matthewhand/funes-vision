#!/bin/bash

IMAGE_DIR=$1

# Ensure the template index.html is copied on startup
cp /home/user/webcam/index.html "$IMAGE_DIR/index.html"

# Run initial generation of images.json
mapfile -t images < <(ls -t "$IMAGE_DIR" 2>/dev/null | grep -E '\.(jpg|jpeg|png|gif)$')
if [ ${#images[@]} -eq 0 ] || [ -z "${images[0]}" ]; then
    echo "[]" > "$IMAGE_DIR/images.json"
else
    printf '%s\n' "${images[@]}" | jq -R . | jq -s . > "$IMAGE_DIR/images.json"
fi

nice -n 10 inotifywait -m -e create --format '%w%f' "$IMAGE_DIR" | while read new_image
do
    # Check if the new file is an image (by extension)
    if [[ ! $new_image =~ \.(jpg|jpeg|png|gif)$ ]]; then
        echo "Skipping non-image file: $new_image"
        sleep 60
        continue
    fi

    echo "New image detected: $new_image"

    # Copy template SPA
    cp /home/user/webcam/index.html "$IMAGE_DIR/index.html"

    # Get array of all image files, sorted by modification time (newest first)
    mapfile -t images < <(ls -t "$IMAGE_DIR" 2>/dev/null | grep -E '\.(jpg|jpeg|png|gif)$')

    if [ ${#images[@]} -eq 0 ] || [ -z "${images[0]}" ]; then
        echo "[]" > "$IMAGE_DIR/images.json"
    else
        printf '%s\n' "${images[@]}" | jq -R . | jq -s . > "$IMAGE_DIR/images.json"
    fi

    # Sync analysis results if available
    [ -f "/home/user/webcam/analysis.json" ] && cp /home/user/webcam/analysis.json "$IMAGE_DIR/analysis.json"
    [ -f "/home/user/webcam/bursts.json" ] && cp /home/user/webcam/bursts.json "$IMAGE_DIR/bursts.json"

    echo "images.json, index.html, and analysis files have been updated"
    sleep 60 # MH 20240402 buffer the cpu?
done
