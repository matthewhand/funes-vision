# Webcam gallery — how to use it

A two-camera, local-first gallery. Motion stills land on this box, a fast
detector tags who-or-what, and a local vision model adds scene flags
(delivery, porch, dog in the yard). Nothing is uploaded unless you turn
an integration on.

Every picture in this guide is from a **synthetic fixture gallery** —
fictional CCTV stills, not your cameras.

## Open it

| Camera | Address |
|--------|---------|
| Front (driveway / gate) | `http://<host>:8180` |
| Back (yard / dogcam) | `http://<host>:8280` |

On a phone, use **Add to Home Screen**. There is no service worker, so you
will not get a stale offline copy. **Switch camera** from the header if you
opened the other feed. The subtitle is the camera nickname plus a short
timezone (`AEST` here) — not a LAN address.

![Mobile timeline of dog and person visits](guide/img/mobile-timeline.png)

## What you are looking at

**Timeline** is the default. It groups a run of stills into one visit you
can play, instead of a pile of near-identical JPEGs.

![Timeline with a dog visit and a person visit](guide/img/timeline.png)

- **Timeline** — visits (“Dog visit · 18 sec · 2 frames”)
- **Objects** — only frames the detector marked
- **All** — every motion still, including empty ones

Each camera is its own site. They do not share one URL.

## Find something

Use **Filters** for object chips (person, dog, car, bird…). The chips are
built from what the detector has actually seen. Search matches time,
detector labels, and captions — the activity charts and the grid both
honor labels. Tap a day on the activity chart to drill into hours.
Saved searches live in **this browser only**.

![Object filter chips over the gallery](guide/img/filters.png)

## Read a card

Colour badges are detector objects: person, dog, car, cat, bird.

Smaller scene flags (mail, porch, laundry, animal type) come from the
local vision model. They describe *what happened*, they are not extra
species of object.

- A `?` badge (amber) means the fast detector fired and the vision model
  has not confirmed yet — see the `Car?` tag in the grid below.
- A struck-through badge means the two passes disagreed. Hidden unless
  you turn on **Show unconfirmed detections**.

![Objects tab with person, dog, bird, and a preliminary car](guide/img/objects.png)

**All** includes empty stills (labelled CLEAR) and anything still waiting
on analysis:

![All-snapshots grid including empty frames](guide/img/all-grid.png)

On a phone the same tab is a single-column stack:

![Mobile All-snapshots grid](guide/img/mobile-grid.png)

## Play a visit

Tap a Timeline card (or its play button):

- Play / pause the flipbook
- Tap the image to step frame-by-frame
- Arrows, Space, Esc
- **Download GIF** of the visit
- **Copy link** to this moment
- Pin the key frame so retention never deletes it

![Visit player with play, GIF download, and copy-link](guide/img/visit-player.png)

## Lightbox (single frame)

From Objects or All, tap a still for full-resolution review, next/previous,
pin, and delete. Delete is permanent.

![Lightbox reviewing a single driveway still](guide/img/lightbox.png)

## Live updates

The header badge is **Live** (SSE connected), **Stale** (SSE connected
but the heartbeat went quiet), **Polling** (asking the server on an
interval), or **Off**. Flip **Auto-refresh** off to pause the stream and
the polls. The **AI** button opens pipeline status: queue, cameras, last
sweep, and the vision-model audit trail.

![AI status panel](guide/img/status.png)

## Settings you might actually touch

![Settings menu with detector, deep-pass, and interval controls](guide/img/settings.png)

- Hide a noisy label (a parked car that is always in frame)
- Merge face/body → person
- Show / hide unconfirmed tags
- **AI deep passes** — pause all vision-model work (detector-only)
- **Idle backfill** — verifies leftover empties and car-only skips
  (nearest detections first). Leave **off** unless you want the
  archive re-checked (thousands of old empties)
- **Multi-image summaries** — leave **off** unless you want visit captions
  from several frames at once

## Alerts

Slack (if enabled) does **not** always post a sequence. With
**multi-image summaries off** and Slack set to *context*, it will stay
quiet — that is intentional. Switch Slack to *objects* to ping on each
new person/dog detector hit (the vision model does not have to finish).

Home Assistant MQTT is optional and **off unless you enable it**, and
it needs an **explicit broker host** (no default address). It publishes
flags, not JPEGs.

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
- ntfy text (only if you set a server URL; there is no public ntfy.sh default)
- Home Assistant MQTT flag JSON (no image bytes; off unless you enable it
  and set a host)

This guide’s screenshots are generated fiction. They are not your house.
They were captured with `tools/screenshots/run_shots.sh`, which **refuses**
to read `/mnt/models/Webcam21` or `/mnt/models/Webcam22`.

## If it looks wrong

| Symptom | Likely cause |
|---------|----------------|
| Empty Objects tab | No detector hits in the current day/filter |
| One camera looks stale | That camera has not uploaded stills |
| Images vanished | Retention budget / 30-day empty-frame rule (pins survive) |
| Pin / delete / settings do nothing | The write API (`webcam-api`) is down |
| “AI is looking…” never finishes | Local model not loaded, or RAM too tight |
| Slack never pings | Summaries off + Slack mode `context` |

## For operators

Developer / ops reference: [DEVELOP.md](../DEVELOP.md),
[DEPLOYMENT.md](../DEPLOYMENT.md), [API.md](../API.md).
How the screenshots were taken: [tools/screenshots/README.md](../tools/screenshots/README.md).
