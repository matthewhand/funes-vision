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

- **Timeline tab** (default) — appear/disappear events per object ("car
  disappeared 9:15", "person appeared 7:02"). Tap an event to play it as a
  short animation; tap the image to step frame-by-frame.
- **Objects tab** — only images with AI-verified detections.
- **All tab** — every snapshot.
- **Object buttons** (person, dog, cat, car…) appear automatically for
  whatever the AI has detected. Tap to filter, tap again to clear. Face and
  body detections are merged into **person** by default.
- **Day planner chart** — each bar is a day (top = midnight); red marks show
  *when* the filtered object was seen. Tap a day to drill into its hourly
  histogram below.
- **Mode: Precise / Potential** — Precise shows only LLM-verified hits;
  Potential also includes unverified detector-only hits (badged `label?`).
- **Pin** (📌) an image to protect it from automatic cleanup forever.
  **Delete** (🗑) removes an image from the server permanently.
- **Hidden menu** — hide noisy labels, toggle person-merging, choose the
  fast detector, and turn idle deep analysis on/off.

## What happens automatically

- New snapshots are detected within seconds and queued for analysis.
- People/dogs/cats jump the queue; everything else is verified when idle,
  newest first.
- Old images with no detections are deleted after 30 days, or sooner if a
  camera exceeds its 6GB disk budget. Pinned images and images with
  detections are kept (detections are only pruned under disk pressure).

For architecture, configuration, and operations detail see
[DEVELOP.md](DEVELOP.md).
