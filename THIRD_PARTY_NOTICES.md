# Third-Party Notices

This file lists third-party components distributed with, or required to run,
funes-vision, together with their licenses. It is informational and does not
replace the full license text shipped by each upstream project.

## Bundled / vendored components

### lucide (`lucide.min.js`)

- Version: `v0.469.0`
- License: **ISC**
- Source: https://github.com/lucide-icons/lucide
- Verified from the file header, which reads:

  ```
  /**
   * @license lucide v0.469.0 - ISC
   *
   * This source code is licensed under the ISC license.
   * See the LICENSE file in the root directory of this source tree.
   */
  ```

  The file is vendored into the repository so the UI does not depend on a
  remote CDN (see `tests/test_no_remote_cdn.js`).

## Python runtime dependencies

These are imported directly by the application code (see `ha_mqtt.py`,
`api_server.py`, `analyze_images.py`, `integrations/media.py` and tooling under
`tools/`) and are not vendored — install them via your environment.

| Component | Import | License |
| --- | --- | --- |
| requests | `import requests` | Apache-2.0 |
| opencv-python | `import cv2` | Apache-2.0 |
| numpy | `import numpy` | BSD-3-Clause |
| Pillow | `from PIL import Image` | MIT-CMU (HPND) |

- requests: https://github.com/psf/requests
- opencv-python: https://github.com/opencv/opencv-python (OpenCV itself is Apache-2.0)
- numpy: https://github.com/numpy/numpy
- Pillow: https://github.com/python-pillow/Pillow

## Separately fetched models and data

The following are **not** distributed with this repository. Users fetch them
independently, and they remain subject to their own upstream terms and
licenses:

- **YOLOv4-tiny weights and config** (`yolov4-tiny.{cfg,weights}`, ~24 MB),
  downloaded from the AlexeyAB/darknet GitHub releases and placed under the
  configured `yolo_dir` (see `DEPLOYMENT.md`). Subject to the Darknet/YOLO
  upstream terms.
- **gemma / Ollama model** (e.g. `gemma4:e2b`), pulled by a local Ollama
  server. The model weights and Ollama runtime are governed by Google's Gemma
  terms of use and the Ollama project's license respectively.

## Fixtures and generated assets

- The synthetic screenshot fixture images under `tools/screenshots/fixtures/`
  and `tools/demo/fixtures/` are **original, generated artwork** (fictional
  CCTV stills) created for this project. They are not real camera captures and
  are covered by this repository's MIT license. See
  `tools/screenshots/make_fixtures.py`, which documents that it assembles
  synthetic stills and never reads real camera storage.
