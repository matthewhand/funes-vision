"""Versioned analysis.json row contract (schema 1).

Classifies the flat on-disk keys the SPA already reads (`person`,
`fast_pass`, `_llm`, `_llm_skip`, `_yolo`, HA flags). Does not import
analyze_images (that pulls OpenCV). Does not invent HA flags.
"""

SCHEMA_VERSION = 1

# Duplicated from analyze_images so this module stays import-light.
YOLO_PRESENCE_KEYS = ("person", "dog", "car", "cat", "bird", "face", "body")
GATE_IGNORE_LABELS = ("car",)

KINDS = ("verified", "no_trigger", "negative", "skip", "preliminary", "empty")


def detector_true_labels(rec):
    """Sorted YOLO keys that are True. Never HA flag names."""
    rec = rec if isinstance(rec, dict) else {}
    return sorted(k for k in YOLO_PRESENCE_KEYS if rec.get(k) is True)


def stamp(rec):
    """Return a copy with `_schema` and, if missing, `_yolo`. Non-dicts pass through."""
    if not isinstance(rec, dict):
        return rec
    out = dict(rec)
    out["_schema"] = SCHEMA_VERSION
    if "_yolo" not in out:
        out["_yolo"] = detector_true_labels(out)
    return out


def kind(rec):
    """One of KINDS. Precedence: verified, no_trigger, negative, skip, preliminary, empty."""
    if not isinstance(rec, dict):
        return "empty"
    skip = rec.get("_llm_skip")
    llm = rec.get("_llm")
    if isinstance(llm, dict) and bool(llm) and not skip:
        return "verified"
    if skip == "no_trigger":
        return "no_trigger"
    if rec.get("fast_pass") == "negative":
        return "negative"
    if skip:
        return "skip"
    if rec.get("fast_pass") == "partial":
        return "preliminary"
    if detector_true_labels(rec) and not llm and "fast_pass" not in rec:
        return "preliminary"
    return "empty"


def is_llm_verified(rec):
    return kind(rec) == "verified"


def is_timeline_persistable(rec, gate_ignore=GATE_IGNORE_LABELS):
    """LLM-verified visit that may outlive max_age_days.

    Motion JPEGs and YOLO-only hits do not qualify. Needs a successful
    `_llm` merge (no skip) plus either a True HA activity flag or a
    detector label that is not gate-ignored (default: car). Capped later
    by `persist_budget_pct` so the archive cannot fill the camera dir.
    """
    if not is_llm_verified(rec):
        return False
    ignore = set(gate_ignore or ())
    llm = rec.get("_llm") if isinstance(rec.get("_llm"), dict) else {}
    for src in (rec, llm):
        for k, v in src.items():
            if v is True and k not in YOLO_PRESENCE_KEYS and k != "fast_pass" \
                    and not str(k).startswith("_"):
                return True
    return any(k not in ignore for k in detector_true_labels(rec))


def is_awaiting_backfill(rec):
    return kind(rec) in ("negative", "no_trigger")


def in_backfill_pool(rec, gate_ignore=GATE_IGNORE_LABELS):
    """Idle-backfill candidates: awaiting_backfill, or ignore-only partials.

    Matches analyze_images.in_backfill_pool: fast_pass==partial and every
    True key is in gate_ignore (default: car). Urgent skip-partials stay out.
    """
    if not isinstance(rec, dict) or is_llm_verified(rec):
        return False
    if is_awaiting_backfill(rec):
        return True
    ignore = set(gate_ignore)
    return rec.get("fast_pass") == "partial" and all(
        k in ignore for k, v in rec.items() if v is True
    )
