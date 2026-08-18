#!/usr/bin/env python3
"""Assemble the synthetic screenshot gallery.

Copies generated (fictional) CCTV stills into Hikvision-style names,
writes catalog JSON, and builds thumbs. Never reads /mnt/models/Webcam*.

Usage:
  python3 tools/screenshots/make_fixtures.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GALLERY = os.path.join(HERE, "fixtures", "gallery")
EMPTY = os.path.join(HERE, "fixtures", "empty")
API = os.path.join(HERE, "fixtures", "api")
SRC = os.environ.get(
    "FIXTURE_SRC",
    os.path.expanduser(
        "~/.grok/sessions/%2Fhome%2Fuser%2Fwebcam/"
        "01a00f33-fd31-72c0-9a28-8ca5dda45aac/images"
    ),
)

# Fictional demo day: 18 Jun 2026 (Australia/Sydney). Not live camera times.
FRAMES = [
    # newest first in images.json
    {
        "name": "10.0.0.21_01_20260618170000000_MOTDEC.jpg",
        "src": "2.jpg",
        "role": "empty front evening (CLEAR)",
        "analysis": {"fast_pass": "negative"},
    },
    {
        "name": "10.0.0.22_01_20260618143028000_MOTDEC.jpg",
        "src": "8.jpg",
        "role": "dog visit end",
        "analysis": {
            "dog": True,
            "_yolo": ["dog"],
            "animal_detected": True,
            "animal_type": "dog",
            "dog_walked": False,
            "approaching_house": False,
            "leaving_house": False,
            "weapon_detected": False,
            "clothes_drying": False,
            "_llm": {"animal_detected": True, "animal_type": "dog"},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 41200,
            "description": "A tan dog standing on the back lawn.",
        },
    },
    {
        "name": "10.0.0.22_01_20260618143010000_MOTDEC.jpg",
        "src": "6.jpg",
        "role": "dog visit start",
        "analysis": {
            "dog": True,
            "_yolo": ["dog"],
            "animal_detected": True,
            "animal_type": "dog",
            "_llm": {"animal_detected": True, "animal_type": "dog"},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 39800,
            "description": "A tan dog in the backyard near the garden bed.",
        },
    },
    {
        "name": "10.0.0.22_01_20260618120000000_MOTDEC.jpg",
        "src": "3.jpg",
        "role": "bird",
        "analysis": {
            "bird": True,
            "_yolo": ["bird"],
            "animal_detected": True,
            "animal_type": "bird",
            "_llm": {"animal_detected": True, "animal_type": "bird"},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 38100,
            "description": "Magpies on the back fence.",
        },
    },
    {
        "name": "10.0.0.21_01_20260618101522301_MOTDEC.jpg",
        "src": "7.jpg",
        "role": "person visit end / burst id / pin",
        "analysis": {
            "person": True,
            "_yolo": ["person"],
            "postal_delivery": True,
            "postal_how": "on foot",
            "porch_access": True,
            "dog_walked": False,
            "car_access": False,
            "enters_car": False,
            "exits_car": False,
            "car_outfit": "none",
            "car_color": "none",
            "car_make": "none",
            "opens_box": False,
            "animal_detected": False,
            "animal_type": "none",
            "_llm": {
                "postal_delivery": True,
                "postal_how": "on foot",
                "porch_access": True,
            },
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 44100,
            "description": "Courier walking a parcel up to the front door.",
        },
    },
    {
        "name": "10.0.0.21_01_20260618101445190_MOTDEC.jpg",
        "src": "4.jpg",
        "role": "person visit mid",
        "analysis": {
            "person": True,
            "_yolo": ["person"],
            "postal_delivery": True,
            "postal_how": "on foot",
            "porch_access": True,
            "_llm": {"postal_delivery": True, "postal_how": "on foot", "porch_access": True},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 43000,
            "description": "A person carrying a parcel on the front driveway.",
        },
    },
    {
        "name": "10.0.0.21_01_20260618101418112_MOTDEC.jpg",
        "src": "4.jpg",
        "role": "person visit start",
        "analysis": {
            "person": True,
            "_yolo": ["person"],
            "postal_delivery": True,
            "postal_how": "on foot",
            "porch_access": False,
            "_llm": {"postal_delivery": True, "postal_how": "on foot"},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 42800,
            "description": "A person with a parcel entering the driveway.",
        },
    },
    {
        "name": "10.0.0.21_01_20260618093015000_MOTDEC.jpg",
        "src": "5.jpg",
        "role": "car preliminary",
        "analysis": {
            "car": True,
            "_yolo": ["car"],
            "fast_pass": "partial",
        },
    },
    {
        "name": "10.0.0.22_01_20260618080000000_MOTDEC.jpg",
        "src": "1.jpg",
        "role": "empty backyard",
        "analysis": {"fast_pass": "negative"},
    },
    {
        "name": "10.0.0.21_01_20260618070200000_MOTDEC.jpg",
        "src": "2.jpg",
        "role": "empty front",
        "analysis": {"fast_pass": "negative"},
    },
]


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def osd_from_name(name):
    """CCTV OSD matching the Hikvision filename clock (not the baked-in 2024 date)."""
    m = re.match(r"(10\.0\.0\.\d+)_(\d+)_(\d{14})", name)
    if not m:
        return None
    ip, _ch, stamp = m.group(1), m.group(2), m.group(3)
    ts = datetime.strptime(stamp, "%Y%m%d%H%M%S")
    cam = "CAM 01 FRONT" if ip.endswith(".21") else "CAM 02 BACK"
    return ts.strftime("%Y-%m-%d %H:%M:%S"), cam


def stamp_osd(img, name):
    """Cover the generator's 2024 OSD and draw a filename-matching clock."""
    lines = osd_from_name(name)
    if not lines:
        return img
    h, w = img.shape[:2]
    y1 = max(8, int(h * 0.20))
    x0 = int(w * 0.48)
    roi = img[0:y1, x0:w]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2)
    if int(cv2.countNonZero(mask)) > 20:
        img[0:y1, x0:w] = cv2.inpaint(roi, mask, 4, cv2.INPAINT_TELEA)
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = max(0.45, w / 1280.0 * 0.55)
    thick = 1
    pad = 10
    sizes = [cv2.getTextSize(t, font, scale, thick)[0] for t in lines]
    tw = max(s[0] for s in sizes)
    line_h = max(s[1] for s in sizes) + 6
    x = w - tw - pad
    y = pad + sizes[0][1]
    for i, text in enumerate(lines):
        yy = y + i * line_h
        cv2.putText(img, text, (x, yy), font, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(img, text, (x, yy), font, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return img


def resize_write(src, dest, max_w, stamp_name=None):
    img = cv2.imread(src)
    if img is None:
        die(f"could not read {src}")
    if stamp_name:
        img = stamp_osd(img, stamp_name)
    h, w = img.shape[:2]
    if w > max_w:
        scale = max_w / float(w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cv2.imwrite(dest, img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])


def main():
    if not os.path.isdir(SRC):
        die(f"FIXTURE_SRC not found: {SRC}\nGenerate fictional stills first.")
    for d in (GALLERY, os.path.join(GALLERY, "thumbs"), EMPTY, API):
        os.makedirs(d, exist_ok=True)

    images = []
    analysis = {}
    for fr in FRAMES:
        src = os.path.join(SRC, fr["src"])
        if not os.path.isfile(src):
            die(f"missing source still {src}")
        dest = os.path.join(GALLERY, fr["name"])
        thumb = os.path.join(GALLERY, "thumbs", fr["name"])
        resize_write(src, dest, 1280, stamp_name=fr["name"])
        # Stamp on the full still first, then shrink — thumbs inherit the clock.
        img_full = cv2.imread(dest)
        if img_full is None:
            die(f"could not reread {dest}")
        th, tw = img_full.shape[:2]
        if tw > 480:
            scale = 480 / float(tw)
            img_full = cv2.resize(
                img_full, (int(tw * scale), int(th * scale)), interpolation=cv2.INTER_AREA
            )
        cv2.imwrite(thumb, img_full, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        images.append(fr["name"])
        if fr["analysis"] is not None:
            analysis[fr["name"]] = fr["analysis"]

    bursts = {
        "10.0.0.21_01_20260618101522301_MOTDEC.jpg": {
            "summary": "A courier walks a parcel up the front driveway to the door.",
            "images": [
                "10.0.0.21_01_20260618101418112_MOTDEC.jpg",
                "10.0.0.21_01_20260618101445190_MOTDEC.jpg",
                "10.0.0.21_01_20260618101522301_MOTDEC.jpg",
            ],
        },
        "10.0.0.22_01_20260618143028000_MOTDEC.jpg": {
            "summary": "A tan dog moves across the back lawn toward the garden bed.",
            "images": [
                "10.0.0.22_01_20260618143010000_MOTDEC.jpg",
                "10.0.0.22_01_20260618143028000_MOTDEC.jpg",
            ],
        },
    }
    pins = ["10.0.0.21_01_20260618101522301_MOTDEC.jpg"]

    write_json(os.path.join(GALLERY, "images.json"), images)
    write_json(os.path.join(GALLERY, "analysis.json"), analysis)
    write_json(os.path.join(GALLERY, "bursts.json"), bursts)
    write_json(os.path.join(GALLERY, "pins.json"), pins)

    write_json(os.path.join(EMPTY, "images.json"), [])
    write_json(os.path.join(EMPTY, "analysis.json"), {})
    write_json(os.path.join(EMPTY, "bursts.json"), {})
    write_json(os.path.join(EMPTY, "pins.json"), [])

    write_json(os.path.join(API, "settings.json"), {
        "fast_pass_engine": "yolo",
        "deep_backfill": False,
        "deep_passes_enabled": True,
        "burst_summaries_enabled": False,
        "idle_sweep_seconds": 60,
    })
    write_json(os.path.join(API, "status.json"), {
        "timezone": "Australia/Sydney",
        "watch_dirs": ["fixtures/gallery"],
        "settings": {
            "fast_pass_engine": "yolo",
            "deep_backfill": False,
            "deep_passes_enabled": True,
            "burst_summaries_enabled": False,
            "gate_ignore_labels": ["car"],
            "max_dir_gb": 5.0,
            "max_scans_per_image": 2,
            "model_primary": "gemma4:e2b",
        },
        "trigger": {
            "inotify_active": True,
            "idle_sweep_seconds": 60,
            "last_sweep_age_s": 18,
        },
        "llm": {"model": "gemma4:e2b", "reachable": True, "allow_cloud": False},
        "inference": {},
        "queue": {
            "images_on_disk": len(images),
            "unanalyzed": 0,
            "unverified_partials": 1,
            "awaiting_backfill": 3,
            "llm_verified": 6,
            "deep_s_per_frame": 42.0,
            "deep_eta_s": 42,
        },
        "cameras": [
            {
                "name": "front",
                "images": 6,
                "bytes": 900000,
                "budget_pct": 4,
                "last_frame_age_s": 120,
                "stale": False,
            },
            {
                "name": "back",
                "images": 4,
                "bytes": 600000,
                "budget_pct": 3,
                "last_frame_age_s": 240,
                "stale": False,
            },
        ],
        "filesystem": {"free_gb": 42.0, "total_gb": 100.0, "used_pct": 58.0},
        "metrics": {
            "window_min": 60,
            "count": 6,
            "ok": 6,
            "failures": 0,
            "success_rate": 1.0,
            "local": 6,
            "cloud": 0,
            "avg_s": 42.0,
            "p95_s": 45.0,
        },
        "retention": {
            "ts": int(time.time()) - 1800,
            "dir": "fixtures",
            "count": 0,
            "bytes_freed": 0,
        },
    })
    write_json(os.path.join(API, "inference_log.json"), [
        {
            "image": "10.0.0.21_01_20260618101522301_MOTDEC.jpg",
            "model": "gemma4:e2b",
            "trigger": "priority",
            "started": 1781769700,
            "duration_s": 44.1,
            "labels": ["person", "postal_delivery"],
            "ok": True,
        },
        {
            "image": "10.0.0.22_01_20260618143028000_MOTDEC.jpg",
            "model": "gemma4:e2b",
            "trigger": "priority",
            "started": 1781769800,
            "duration_s": 41.2,
            "labels": ["dog"],
            "ok": True,
        },
    ])
    write_json(os.path.join(API, "integrations.json"), {
        "slack": {
            "enabled": False,
            "has_bot_token": False,
            "has_app_token": False,
            "channel_id": "",
            "public_base_url": "",
            "notify_mode": "context",
            "last_delivery": None,
        }
    })
    print(f"wrote {len(images)} frames to {GALLERY}")
    print("catalogs: gallery + empty + api stubs")


if __name__ == "__main__":
    main()
