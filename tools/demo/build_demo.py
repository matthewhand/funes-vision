#!/usr/bin/env python3
"""Build a static, hostable gallery demo from synthetic screenshot fixtures.

Output: dist/demo/  (drop on GitHub Pages / Netlify / `python3 -m http.server`)

Never copies /mnt/models. Stills come from tools/screenshots/fixtures/gallery/.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_GALLERY = ROOT / "tools" / "screenshots" / "fixtures" / "gallery"
FIXTURE_API = ROOT / "tools" / "screenshots" / "fixtures" / "api"
DEMO_SRC = Path(__file__).resolve().parent
OUT = ROOT / "dist" / "demo"

CANONICAL = "https://github.com/matthewhand/webcam"

SENTINEL = (
    '<meta name="robots" content="noindex,nofollow">\n'
    f'<link rel="canonical" href="{CANONICAL}">\n'
    '<script>window.__IS_DEMO__=true;</script>\n'
    '<link rel="stylesheet" href="./static/demo-banner.css">\n'
    '<script src="./static/demo-shim.js"></script>\n'
)


def _load(path: Path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def _split_images(images: list[str]) -> tuple[list[str], list[str]]:
    front = [n for n in images if n.startswith("10.0.0.21_")]
    back = [n for n in images if n.startswith("10.0.0.22_")]
    return front, back


def _catalog(images, analysis, bursts, pins) -> dict:
    names = set(images)
    ana = {k: v for k, v in analysis.items() if k in names}
    bur = {k: v for k, v in bursts.items() if k in names}
    pin = [p for p in pins if p in names]
    thumb = f"thumbs/{images[0]}" if images else None
    return {
        "images": images,
        "analysis": ana,
        "bursts": bur,
        "pins": pin,
        "thumbUrl": thumb,
    }


def network_payload() -> dict:
    images = _load(FIXTURE_GALLERY / "images.json", [])
    analysis = _load(FIXTURE_GALLERY / "analysis.json", {})
    bursts = _load(FIXTURE_GALLERY / "bursts.json", {})
    pins = _load(FIXTURE_GALLERY / "pins.json", [])
    front, back = _split_images(images)
    cat21 = _catalog(front, analysis, bursts, pins)
    cat22 = _catalog(back, analysis, bursts, pins)
    cameras = [
        {"id": "Webcam21", "kind": "front", "label": "Front",
         "source_dir": "fixtures/gallery", "index": 0},
        {"id": "Webcam22", "kind": "back", "label": "Back",
         "source_dir": "fixtures/gallery", "index": 1},
    ]
    settings = _load(FIXTURE_API / "settings.json", {})
    settings = dict(settings)
    settings["cameras"] = cameras
    status = _load(FIXTURE_API / "status.json", {})
    integrations = _load(FIXTURE_API / "integrations.json", {})
    inference = _load(FIXTURE_API / "inference_log.json", [])
    schema = _load(FIXTURE_API / "llm-schema.json", {})
    health = {
        "status": "ok",
        "fixture": True,
        "checks": {
            "inotify": True,
            "llm_reachable": True,
            "recent_sweep": True,
            "disk_space": True,
        },
        "cameras": cameras,
    }
    static = {
        "/api/status": {"body": status, "delay": 40},
        "/api/health": {"body": health, "delay": 20},
        "/api/settings": {"body": settings, "delay": 30},
        "/api/cameras": {"body": cameras, "delay": 30},
        "/api/integrations": {"body": integrations, "delay": 30},
        "/api/inference_log": {"body": inference, "delay": 30},
        "/api/llm-schema": {"body": schema, "delay": 30},
        "/api/pins": {"body": pins, "delay": 20},
        "/api/catalogs": {
            "body": {"Webcam21": cat21, "Webcam22": cat22},
            "delay": 50,
        },
        "/api/catalogs?camera=Webcam21": {"body": cat21, "delay": 50},
        "/api/catalogs?camera=Webcam22": {"body": cat22, "delay": 50},
        "/api/catalogs?camera=all": {
            "body": {"Webcam21": cat21, "Webcam22": cat22},
            "delay": 50,
        },
    }
    return {"static": static, "decks": {}}


def inject_html(src: str) -> str:
    html = src
    head = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if head:
        html = html[: head.end()] + "\n" + SENTINEL + html[head.end() :]
    else:
        html = SENTINEL + html
    return html


def build(out: Path = OUT) -> Path:
    if "Webcam21" in str(FIXTURE_GALLERY) or "/mnt/models" in str(FIXTURE_GALLERY):
        raise SystemExit("refusing to build from a live camera path")
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    (out / "fixtures").mkdir()
    (out / "thumbs").mkdir()

    html = (ROOT / "index.html").read_text()
    (out / "index.html").write_text(inject_html(html))

    for name in ("lucide.min.js", "favicon.ico", "icon.svg", "manifest.json"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, out / name)

    for jpg in FIXTURE_GALLERY.glob("*.jpg"):
        shutil.copy2(jpg, out / jpg.name)
    thumbs = FIXTURE_GALLERY / "thumbs"
    if thumbs.is_dir():
        for jpg in thumbs.glob("*.jpg"):
            shutil.copy2(jpg, out / "thumbs" / jpg.name)
    for name in ("images.json", "analysis.json", "bursts.json", "pins.json"):
        shutil.copy2(FIXTURE_GALLERY / name, out / name)

    shutil.copy2(DEMO_SRC / "static" / "demo-shim.src.js", out / "static" / "demo-shim.js")
    shutil.copy2(DEMO_SRC / "static" / "demo-banner.css", out / "static" / "demo-banner.css")
    shutil.copy2(DEMO_SRC / "static" / "demo-banner.html", out / "static" / "demo-banner.html")

    (out / "fixtures" / "network.json").write_text(
        json.dumps(network_payload(), indent=2) + "\n"
    )
    (out / "fixtures" / "ticker.json").write_text(
        json.dumps({"stage_timeline": [], "completed_pool": [], "fake_stats": {}}, indent=2)
        + "\n"
    )
    (out / "README.md").write_text(
        (DEMO_SRC / "README.md").read_text() if (DEMO_SRC / "README.md").is_file() else ""
    )
    return out


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    path = build(dest)
    print(f"demo bundle: {path}")
