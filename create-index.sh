#!/bin/bash

IMAGE_DIR=$1

# Function to generate an img tag with specified size
generate_img_tag() {
    local image_file=$1
    local size_class=$2
    echo "<img src=\"$image_file\" class=\"$size_class\" />"
}

# Function to generate a text link for an image
generate_text_link() {
    local image_file=$1
    echo "<a href=\"$image_file\">$image_file</a><br>"
}

nice -n 10 inotifywait -m -e create --format '%w%f' "$IMAGE_DIR" | while read new_image
do
    # Check if the new file is an image (by extension)
    if [[ ! $new_image =~ \.(jpg|jpeg|png|gif)$ ]]; then
        echo "Skipping non-image file: $new_image"
	sleep 60
        continue
    fi

    echo "New image detected: $new_image"

    # Start building the HTML
    html="<html><head><style>"
    html+="img.large { width: 100%; } "
    html+="img.medium { width: 50%; } "
    html+="img.small { width: 25%; } "  # Define small image size
    html+=".scroll-pane { height: 200px; overflow-y: scroll; }"
    html+="</style></head><body>"

    # Get an array of all image files, sorted by modification time (newest first),
    # filtering out non-image files using grep with a regex pattern
    mapfile -t images < <(ls -t "$IMAGE_DIR" | grep -E '\.(jpg|jpeg|png|gif)$')

    # Add the most recent image as very large
    if [ -n "${images[0]}" ]; then
        html+="$(generate_img_tag "${images[0]}" "large")"
    fi

    # Adjust to add the next four images as medium
    for i in {1..4}; do
        if [ -n "${images[i]}" ]; then
            html+="$(generate_img_tag "${images[i]}" "medium")"
        fi
    done

    # Add the next ten images as small
    for i in {5..16}; do
        if [ -n "${images[i]}" ]; then
            html+="$(generate_img_tag "${images[i]}" "small")"
        fi
    done

    # Start the scroll pane for remaining images
    html+="<div class=\"scroll-pane\">"

    # Adjust to add text links for the images after the first 15
    for ((i=17; i<${#images[@]}; i++)); do
        if [ -n "${images[i]}" ]; then
            html+="$(generate_text_link "${images[i]}")"
        fi
    done

    # Close the scroll pane div
    html+="</div>"

    # Finish the HTML
    html+="</body></html>"

    # Write the HTML to the index.html file
    echo "$html" > "$IMAGE_DIR/index.html"

    echo "index.html has been updated"
    sleep 60 # MH 20240402 buffer the cpu?
done

