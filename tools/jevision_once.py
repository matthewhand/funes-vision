#!/usr/bin/env python3
"""One-shot JEVision typed image decision.

Reads one JSON request from stdin and writes one System One-compatible JSON
response to stdout. The process exits after the request so model residency is
released immediately.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image


def _load_runtime(root: Path):
    runtime = str(root / "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from kev.api import SystemOneRequest, to_answers, to_record  # type: ignore
    from kev.visual_model import VisualDecisionModel  # type: ignore
    return SystemOneRequest, to_answers, to_record, VisualDecisionModel


def _bounded_image(path: str, max_width: int, max_height: int) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return image


def main() -> int:
    req = json.load(sys.stdin)
    root = Path(req["root"]).resolve()
    image_path = os.path.abspath(req["image_path"])
    max_width = max(32, int(req.get("max_width", 384)))
    max_height = max(32, int(req.get("max_height", 288)))

    SystemOneRequest, to_answers, to_record, VisualDecisionModel = _load_runtime(root)
    request = SystemOneRequest(
        state=req.get("state"),
        model="jevision",
        questions=req["questions"],
    )
    record, meta = to_record(request)
    image = _bounded_image(image_path, max_width, max_height)
    model = VisualDecisionModel.from_run(root, "cpu", max_input_tokens=80000)
    probs = [p.tolist() for p in model.probs(record, [image])]
    answers = to_answers(probs, meta)
    out = {
        "model": "jevision-0.8b",
        "answers": answers,
        "usage": model.last_usage,
        "image_size": list(image.size),
    }
    json.dump(out, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
