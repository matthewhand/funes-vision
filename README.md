# Funes Vision

**The camera notices motion. Funes helps you find what mattered.**

Inspired by *Funes the Memorious*, the short story by Jorge Luis Borges about remembering too much. Funes Vision aims to turn motion-triggered stills into a useful, browsable history.

![Actual synthetic end-to-end proof: watched folder starts empty, ten fake camera JPEGs appear, and the visit timeline groups them](docs/evidence/watched-folder-proof.gif)

*Real browser capture of the Python folder-watcher: empty → 10 fictional images → grouped visit filtering. Not real surveillance or model inference.*

## Quick start: one server, one port

**Python 3.10+ is the canonical MVP preview server.** It serves the browser UI, read-only JSON API and watched JPEG files together on `127.0.0.1:8000`. Node.js is optional for static frontend development—not a second production service.

```sh
git clone https://github.com/matthewhand/funes-vision.git
cd funes-vision
python3 -m funes.server
```

Open **http://127.0.0.1:8000/**. At first, the watched-folder activity view is empty. In **another terminal**, from the same checkout, simulate a motion-detecting camera uploading image files:

```sh
python3 tools/seed_synthetic_watch.py
```

The browser updates automatically: **10 JPEGs**, grouped into **7 visits**. For instance, the visitor's three frames form one episode, rather than three unrelated visits. There is no model download, inference, API token or camera connection involved. The sample frames and annotations are fictional and hash-checked. Stop the Python server with `Ctrl+C`.

<details>
<summary><strong>Docker Compose</strong> — same Python API, same port</summary>

```sh
mkdir -p runtime/incoming/DemoFront runtime/incoming/DemoBack
docker compose up --build
```

Open **http://127.0.0.1:8000/**; in another terminal run `python3 tools/seed_synthetic_watch.py` to populate the locally mounted synthetic folder. To stop: `Ctrl+C` followed by `docker compose down` if necessary.

`compose.yml` publishes **loopback only** and mounts just `./runtime/incoming` as read-only inside a restricted non-root container. No live cameras are mounted. Docker runs the **Python** server, not Node.js. Images and host settings under `runtime/` are gitignored.

</details>

<details>
<summary><strong>Node.js 20+</strong> — optional static UI development</summary>

```sh
npm start
```

This serves the original static, fictional-data frontend with **no npm dependencies**. It does not scan a watched folder or provide the Python `/api/activity` implementation. Do not run Node and Python simultaneously on port 8000.

</details>

## How it works

![Funes Vision architecture diagram: a motion-detecting camera saves JPEGs to a folder, followed by indexing, YOLO object detection, optional multi-frame decision analysis, and searchable visits](docs/architecture-overview.svg)

**The camera does the motion detection** and deposits JPEG snapshots into a watched folder (for example via FTP or SFTP). Funes does not require a continuous video stream. The architecture separates three levels of understanding:

| Level | What it means | Example |
|---|---|---|
| **Motion frame** | One JPEG written by the motion-detecting camera; Funes sees a newly saved file | `10:14:18`, person carrying a parcel |
| **Object detection** | A fast model such as YOLO would identify candidate objects in **an individual frame**, with its own detector-derived labels | `person` or `car`, before any contextual conclusion |
| **Visit / timeline** | Multiple frames from the same camera in a time window are grouped into a **single episode**, retaining all images and object evidence | Three frames at 10:14:18, 10:14:45 and 10:15:22 form one visitor event |

An optional **System1 decision model or vision LLM** can evaluate selected individual images *or a sequence of frames from a visit*, adding structured interpretations such as *approaching the entrance* or *leaving*. This is deeper scene/temporal interpretation, not the same task as object detection. It must not silently overwrite the original detector labels. Models are optional; the archive can function without one.

**What runs today:** the published proof scans a local watched folder, displays the JPEGs and groups them into visits. Its illustrative object labels come from reviewed synthetic metadata, **not actual YOLO/System1/LLM execution**. The live object-detector and decision-model integrations are still future MVP gates.

## Visual proof: watched-folder activity

![Actual browser view of synthetic camera frames and grouped visit timeline after inserting 10 JPEGs into a watched folder](docs/evidence/folder-activity-desktop.png)

<details><summary>Mobile capture from the same Python server</summary>

![390px mobile preview of synthetic watched-folder visit grouping](docs/evidence/folder-activity-mobile.png)

</details>

The dashboard also works at phone widths. For the existing Refined Current interface with fixture catalogs, open **http://127.0.0.1:8000/frontend/index.html**. For the compact synthetic stills gallery, use `/demo/index.html`. Both are served by the **same Python API process**.

## Small-hardware design target

The intended reference deployment is **a two-core ARM CPU with 12 GB of RAM**. That's a *design constraint*, not a benchmark or minimum requirement proven for every detector/model combination.

- Motion detection runs on the **camera**, so Funes receives intermittent JPEGs instead of decoding a continuous stream.
- File indexing and timeline rendering should remain lightweight and work **without any model loaded**; the current Python preview uses only standard-library modules and bounded folder scans.
- The object detector is intended to be a comparatively cheap first pass. Expensive decision/vision inference is **optional and selective**, ideally processing a relevant frame or limited visit sequence at a time.
- On small ARM systems, model startup, concurrent inferences, large context windows and full backfills can exhaust CPU or RAM. Inference performance and limits must be measured on the target hardware before we publish resource or throughput claims.

## Verify from a clean checkout

```sh
python3 -m unittest discover -s tests -p test_watched_folder.py
python3 tests/check_synthetic_assets.py
python3 tests/check_refined_frontend.py
node --test tests/preview-server.test.mjs  # optional Node.js static preview checks
```

See the repository's [CI](.github/workflows/synthetic-fixtures.yml) for automated fixture, Docker and single-origin backend smoke tests. The offline gallery and activity view are safe synthetic proofs; they are **not** a deployed production camera-analysis system.

MIT license. Bundled Lucide icons are ISC; see [third-party notices](THIRD_PARTY_NOTICES.md). All supplied image examples are fictional and AI-generated.
