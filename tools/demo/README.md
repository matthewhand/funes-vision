# Static demo gallery

A **hostable** copy of the SPA that talks to **synthetic fixtures**, not live
cameras. No `/mnt/models`, OpenCV, Ollama, or write API.

Stills are the fictional screenshot fixtures (`tools/screenshots/fixtures/gallery/`).
Do not replace them with real Webcam21/22 frames.

## Build

From the repo root:

```sh
python3 tools/demo/build_demo.py
```

Writes `dist/demo/`.

## Run locally

```sh
python3 -m http.server 8765 --directory dist/demo
# open http://127.0.0.1:8765/
```

Timeline / Objects / All, lightbox, visits, and Live (canned SSE pings) work.
Pin, delete, and settings POSTs toast *Demo: change won't persist* and do not
write disk.

## Host

Drop `dist/demo/` on any static host (GitHub Pages, Netlify, nginx `alias`).
The bundle is `noindex`. Captions and HA flags are **canned samples**, not
live vision-LLM output.

The demo banner links at [the real repo](https://github.com/matthewhand/webcam).
