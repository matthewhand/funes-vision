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
SCREENSHOTS = ROOT / "tools" / "screenshots"
DEMO_SRC = Path(__file__).resolve().parent
OUT = ROOT / "dist" / "demo"

# One definition of the fixture-backed /api payloads, shared with the
# screenshot stub (tools/screenshots/proxy.py). The bundle used to hardcode its
# own camera list and omit /api/taxonomy entirely, so a visitor exercised the
# SPA's offline fallback instead of the real endpoint.
if str(SCREENSHOTS) not in sys.path:
    sys.path.insert(0, str(SCREENSHOTS))
import fixture_api  # noqa: E402  (needs SCREENSHOTS on sys.path first)

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


def network_payload() -> dict:
    gallery = str(FIXTURE_GALLERY)
    cams = fixture_api.cameras(gallery)
    settings = _load(FIXTURE_API / "settings.json", {})
    settings = dict(settings)
    settings["cameras"] = cams
    status = _load(FIXTURE_API / "status.json", {})
    integrations = _load(FIXTURE_API / "integrations.json", {})
    inference = _load(FIXTURE_API / "inference_log.json", [])
    schema = _load(FIXTURE_API / "llm-schema.json", {})
    tax = fixture_api.taxonomy()
    if tax is None:
        raise SystemExit("taxonomy.py not importable; cannot build the demo bundle")
    health = {
        "status": "ok",
        "fixture": True,
        "checks": {
            "inotify": True,
            "llm_reachable": True,
            "recent_sweep": True,
            "disk_space": True,
        },
        "cameras": cams,
    }
    static = {
        "/api/status": {"body": status, "delay": 40},
        "/api/health": {"body": health, "delay": 20},
        "/api/settings": {"body": settings, "delay": 30},
        "/api/cameras": {"body": cams, "delay": 30},
        "/api/taxonomy": {"body": tax, "delay": 30},
        "/api/integrations": {"body": integrations, "delay": 30},
        "/api/inference_log": {"body": inference, "delay": 30},
        "/api/llm-schema": {"body": schema, "delay": 30},
        "/api/pins": {"body": _load(FIXTURE_GALLERY / "pins.json", []), "delay": 20},
        "/api/catalogs": {"body": fixture_api.catalogs(gallery), "delay": 50},
    }
    for cam in cams:
        static[f"/api/catalogs?camera={cam['id']}"] = {
            "body": fixture_api.catalogs(gallery, cam["id"]),
            "delay": 50,
        }
    static["/api/catalogs?camera=all"] = {"body": fixture_api.catalogs(gallery), "delay": 50}
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

    # Help walkthrough. index.html's #btn-help targets USER-GUIDE.html at the web
    # root, so the published bundle has to carry it or the button 404s. Copied
    # unconditionally (not best-effort like the PWA icons above) because it is a
    # hard link target in the page, not an optional extra.
    #
    # The guide is NOT self-contained: it pulls ~14 screenshots from guide/img/
    # relative to itself, and those live under docs/ too. The other three
    # deployment targets already ship both (create-index.sh and
    # tools/deploy-webroot.sh cp both; tools/screenshots/proxy.py serves
    # /guide/img/ the same way) -- the demo was the odd one out, which is why the
    # Help button 404ed there and nowhere else.
    #
    # Fixture shots only: the PNGs are Grok-generated screenshots of the synthetic
    # gallery, never live camera frames. tests/test_demo_build.py re-derives the
    # guide's own image list from the copied HTML and fails if one is missing.
    shutil.copy2(ROOT / "docs" / "USER-GUIDE.html", out / "USER-GUIDE.html")
    guide_out = out / "guide" / "img"
    guide_out.mkdir(parents=True)
    for png in (ROOT / "docs" / "guide" / "img").glob("*.png"):
        shutil.copy2(png, guide_out / png.name)

    for jpg in FIXTURE_GALLERY.glob("*.jpg"):
        shutil.copy2(jpg, out / jpg.name)
    thumbs = FIXTURE_GALLERY / "thumbs"
    if thumbs.is_dir():
        for jpg in thumbs.glob("*.jpg"):
            shutil.copy2(jpg, out / "thumbs" / jpg.name)
    for name in ("images.json", "analysis.json", "bursts.json", "pins.json"):
        shutil.copy2(FIXTURE_GALLERY / name, out / name)

    demo_gif = DEMO_SRC / "demo.gif"
    if demo_gif.is_file():
        shutil.copy2(demo_gif, out / "demo.gif")

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
