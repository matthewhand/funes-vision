# Webcam gallery — how to use it

A two-camera, local-first gallery. Motion stills land on this box, a fast
detector tags who-or-what, and a local vision model adds scene flags
(delivery, porch, dog in the yard). Nothing is uploaded unless you turn
an integration on.

The pictures in this guide are from a **synthetic fixture gallery** — fictional
CCTV stills, not your cameras.

## Open it

| Camera | Address |
|--------|---------|
| Front (driveway / gate) | `http://<host>:8180` |
| Back (yard / dogcam) | `http://<host>:8280` |

On a phone, use **Add to Home Screen**. There is no service worker, so you
will not get a stale offline copy.

**Switch camera** from the header if you opened the other feed.

## What you are looking at

- **Timeline** — visits, not a pile of stills. A person on the driveway for
  40 seconds becomes one card you can play.
- **Objects** — only frames the detector marked (person, dog, car, cat, bird).
- **All** — every motion still, including empty ones.

Each camera is its own site. They do not share one URL.

## Find something

- Search box: time, label, or filename.
- **Filters** — object chips built from what the detector has actually seen.
- Time-of-day pills and the slider (collapses when it is all-day).
- Tap a day on the activity chart to drill into hours.
- **Saved searches** live in this browser only.

## Read a card

- Colour badges are detector objects: person, dog, car, cat, bird.
- Smaller scene flags (mail, porch, laundry, animal type) come from the
  local vision model. They are not extra object types.
- A `?` badge means the fast detector fired and the vision model has not
  confirmed yet.
- A struck-through badge means the two passes disagreed. Hidden unless you
  turn on **Show unconfirmed detections**.

## Play a visit

Tap a Timeline card:

- Play / pause the flipbook
- Tap the image to step frame-by-frame
- Arrows, Space, Esc
- **Download GIF** of the visit
- Pin the key frame so retention never deletes it

## Lightbox (single frame)

From Objects or All, tap a still for full-resolution review, next/previous,
pin, and delete (delete is permanent).

## Live updates

The header **Live** switch is auto-refresh. The status panel says whether
the event stream is connected or the page is polling.

## Settings you might actually touch

- Hide a noisy label (a parked car that is always in frame)
- Merge face/body → person
- Show / hide disputed tags
- Pause all vision-model work (detector-only) when the box is busy
- Idle backfill — leave **off** unless you want the archive re-checked
- Multi-image summaries — leave **off** unless you want visit captions
  from several frames at once

## Alerts

Slack (if enabled) can post a visit. With **multi-image summaries off**
and Slack set to *context*, it will stay quiet — that is intentional.
Switch Slack to *objects* if you want a ping on each detected person/dog.

Home Assistant MQTT is optional and **off unless you enable it**. It
publishes flags, not JPEGs.

## What the box does without you

- New motion stills are picked up within seconds
- Empty frames older than 30 days are removed
- Each camera has a disk budget (5 GB); oldest empties go first
- **Pins are forever**
- A watchdog restarts a stuck pipeline

## Privacy

Frames and inference stay on this machine by default. Things that *can*
leave, only if you turn them on:

- Slack messages (and uploaded clips, if configured)
- Home Assistant MQTT flag JSON (no image bytes)

This user guide’s screenshots are generated fiction. They are not your house.

## If it looks wrong

| Symptom | Likely cause |
|---------|----------------|
| Empty Objects tab | No detector hits in the current day/filter |
| One camera looks stale | That camera has not uploaded stills |
| Images vanished | Retention budget / 30-day empty-frame rule (pins survive) |
| Pin / delete / settings do nothing | The write API (`webcam-api`) is down |
| “AI is looking…” never finishes | Local model not loaded, or RAM too tight |

## Screenshots

Captured from the fixture harness (`tools/screenshots/`), never from the
live camera directories.

<!-- shots are copied here after a fixture run -->
