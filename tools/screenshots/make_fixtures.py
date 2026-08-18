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
import shutil
import sys

import cv2

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
        "role": "unanalyzed evening still",
        "analysis": None,
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


def resize_write(src, dest, max_w):
    img = cv2.imread(src)
    if img is None:
        die(f"could not read {src}")
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
        resize_write(src, dest, 1280)
        resize_write(src, thumb, 480)
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
            "unanalyzed": 1,
            "unverified_partials": 1,
            "awaiting_backfill": 2,
            "llm_verified": 6,
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
        "retention": {"ts": 1781769600, "dir": "fixtures", "count": 0, "bytes_freed": 0},
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
