# Webcam AI Gallery

A self-hosted, AI-assisted gallery for two security cameras. Motion snapshots
are analyzed automatically — first by a fast object detector, then verified by
a local vision LLM — so you can answer questions like *"when was a person
here?"* or *"when did the car leave?"* without scrolling through thousands of
near-identical frames.

Everything runs on this box. No images leave the machine.

## Viewing

| Camera | URL |
|--------|-----|
| Webcam (front, car in frame) | `http://<host>:8180` |
| Dogcam | `http://<host>:8280` |

## Using the gallery

- **Timeline tab** (default) — one card per object "visit" ("Person visit
  · 7:02–7:08 · 6 min", with an AI caption of what happened). Tap a visit
  to play it as a short animation; tap the image to step frame-by-frame.
- **Objects tab** — only images with AI-verified detections.
- **All tab** — every snapshot.
- **Object buttons** (person, dog, cat, car…) appear automatically for
  whatever the AI has detected. Tap to filter, tap again to clear. Face and
  body detections are merged into **person** by default.
- **Day planner chart** — each bar is a day (top = midnight); red marks show
  *when* the filtered object was seen. Tap a day to drill into its hourly
  histogram below.
- **Detection lifecycle badges** — solid badge = verified (both AIs agree),
  amber `label?` = preliminary (fast detector only, awaiting the LLM),
  struck-through red = disputed (the two AIs disagree; hidden unless
  "Show unconfirmed detections" is on).
- **Pin** (📌) an image to protect it from automatic cleanup forever.
  **Delete** (🗑) removes an image from the server permanently.
- **Settings menu** (gear icon) — hide noisy labels, label merging,
  show/hide disputed detections, fast-detector choice, idle deep analysis
  on/off, and sweep/poll intervals.
- **AI button** — pulsing amber with elapsed time while the LLM is
  analyzing; tap for pipeline status and the inference audit trail.

## What happens automatically

- New snapshots are detected within seconds and queued for analysis.
- People/dogs/cats jump the queue; everything else is verified when idle,
  newest first.
- Old images with no detections are deleted after 30 days, or sooner if a
  camera exceeds its 6GB disk budget. Pinned images and images with
  detections are kept (detections are only pruned under disk pressure).

For architecture, configuration, and operations detail see
[DEVELOP.md](DEVELOP.md).
