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

The bundle also ships `demo.gif` — an animated person/dog visit built from the
synthetic fixtures. Regenerate it with `python3 tools/screenshots/make_gifs.py`.

## Run locally

```sh
python3 -m http.server 8765 --directory dist/demo
# open http://127.0.0.1:8765/
```

Timeline / Objects / All, lightbox, visits, and Live (canned SSE pings) work.
Pin, delete, and settings POSTs toast *Demo: change won't persist* and do not
write disk.

## The mocked API

`fixtures/network.json` answers every `GET /api/*` the SPA makes, so a visitor
exercises the real endpoint path rather than the SPA's offline fallback:

| Route | Body |
|-------|------|
| `/api/cameras`, `/api/catalogs`, `/api/catalogs?camera=<id>` | derived from the fixture stills by their leading IP |
| `/api/taxonomy` | the app's own `taxonomy.py` `payload()` |
| `/api/status`, `/api/settings`, `/api/integrations`, `/api/inference_log`, `/api/llm-schema`, `/api/pins`, `/api/health` | `fixtures/api/*.json` |

The camera registry and taxonomy come from
`tools/screenshots/fixture_api.py`, shared with the screenshot stub, so the
bundle cannot drift from the app. `tests/test_stub_api_coverage.py` re-derives
the SPA's route list from `index.html` and fails the build if a new endpoint
lands without a fixture body.

## Host

Drop `dist/demo/` on any static host (GitHub Pages, Netlify, nginx `alias`).
The bundle is `noindex`. Captions and HA flags are **canned samples**, not
live vision-LLM output.

The demo banner links at [the real repo](https://github.com/matthewhand/webcam).
