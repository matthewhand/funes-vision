"""Priority one-question e2b scans.

The full front/back HA schemas are too many questions for e2b in one
call (and several flags are systematic false positives). Each scan is a
tiny JSON schema. YOLO gates decide which run; order is priority.
Does not import analyze_images (cv2).
"""

from taxonomy import DROPPED_FLAGS as DROPPED_FLAGS

# Default: at most two vision calls per frame (~40s each).
MAX_SCANS_PER_IMAGE = 2
SCAN_TOKENS = 48

# Dropped from the live path (vision audit / never-true) — canonical list is
# taxonomy.DROPPED_FLAGS, re-exported here for existing callers.

_POSTAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["postal_delivery", "postal_how"],
    "properties": {
        "postal_delivery": {
            "type": "boolean",
            "description": (
                "True only if a uniformed postie or courier is delivering "
                "mail or a parcel right now (hi-vis, postal bag, branded van). "
                "A resident with a suitcase, shopping, or pram is false. "
                "A parked private car is false."
            ),
        },
        "postal_how": {
            "type": "string",
            "enum": ["van", "bike", "on foot", "truck", "scooter", "car",
                     "unknown", "none"],
            "description": "How the courier arrives. none if postal_delivery is false.",
        },
    },
}

_PORCH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["porch_access"],
    "properties": {
        "porch_access": {
            "type": "boolean",
            "description": (
                "True only if a person is standing on the grey tiled porch "
                "against the house (by the deck box, rail, or front door). "
                "The red brick garden path, lawn, driveway, and street are "
                "NOT the porch — those are false."
            ),
        },
    },
}

_PACKAGE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["opens_box"],
    "properties": {
        "opens_box": {
            "type": "boolean",
            "description": (
                "True only if a person is actively opening a parcel, box, "
                "or letterbox. Carrying a bag is false."
            ),
        },
    },
}

_DOG_WALK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dog_walked"],
    "properties": {
        "dog_walked": {
            "type": "boolean",
            "description": (
                "True only if a person AND a dog are both visible and the "
                "person is walking the dog (leash or clearly leading). "
                "A dog with no person, or a person standing near a sitting "
                "dog, is false."
            ),
        },
    },
}

# Priority order. First matching scans run up to MAX_SCANS_PER_IMAGE.
# animal_detected is NOT an LLM scan — YOLO dog/cat/bird is copied through
# (e2b missed a clear yard dog and invented animals on person-only frames).
SCANS = (
    {
        "id": "postal",
        "cameras": ("front",),
        "need_any": ("person", "face", "body"),
        "schema": _POSTAL,
        "num_predict": SCAN_TOKENS,
    },
    {
        "id": "dog_walk",
        "cameras": ("front", "back"),
        "need_any": ("person", "face", "body"),
        "need_all": ("dog",),
        "schema": _DOG_WALK,
        "num_predict": 32,
    },
    {
        "id": "porch",
        "cameras": ("front",),
        "need_any": ("person", "face", "body"),
        "schema": _PORCH,
        "num_predict": 32,
        # Skipped when a mode=gate porch region is on; geometry sets the flag.
    },
    {
        "id": "package",
        "cameras": ("front",),
        "need_any": ("person", "face", "body"),
        "schema": _PACKAGE,
        "num_predict": 32,
    },
)


def yolo_set(fp_labels):
    if isinstance(fp_labels, dict):
        return {k for k, v in fp_labels.items() if v is True}
    return {k for k in (fp_labels or []) if k}


def yolo_animal_seed(fp_labels):
    """Copy detector animal class through — do not spend an e2b call on it."""
    ys = yolo_set(fp_labels)
    for kind in ("dog", "cat", "bird"):
        if kind in ys:
            return {"animal_detected": True, "animal_type": kind}
    return {}


def scans_for(kind, fp_labels, limit=MAX_SCANS_PER_IMAGE, skip=None):
    """Ordered applicable scans for this camera + detector labels.

    ``skip`` is a set of scan ids replaced by a geometry gate (porch).
    """
    ys = yolo_set(fp_labels)
    skip = {str(x) for x in (skip or ()) if x}
    out = []
    for spec in SCANS:
        if kind not in spec["cameras"]:
            continue
        if spec["id"] in skip:
            continue
        need_any = spec.get("need_any") or ()
        if need_any and not (ys & set(need_any)):
            continue
        need_all = spec.get("need_all") or ()
        if need_all and not set(need_all).issubset(ys):
            continue
        out.append(spec)
        if limit and len(out) >= int(limit):
            break
    return out


def union_schema(specs, seed=None):
    props, req = {}, []
    for spec in specs:
        sch = spec.get("schema") or {}
        props.update(sch.get("properties") or {})
        for k in sch.get("required") or []:
            if k not in req:
                req.append(k)
    if seed:
        if "animal_detected" in seed or "animal_type" in seed:
            props["animal_detected"] = {"type": "boolean"}
            props["animal_type"] = {
                "type": "string",
                "enum": ["dog", "cat", "bird", "wildlife", "other", "none"],
            }
            for k in ("animal_detected", "animal_type"):
                if k not in req:
                    req.append(k)
        for k, v in seed.items():
            if not isinstance(k, str) or k.startswith("_") or k in props:
                continue
            if isinstance(v, bool):
                props[k] = {"type": "boolean"}
            elif isinstance(v, str):
                props[k] = {"type": "string"}
            else:
                continue
            if k not in req:
                req.append(k)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": req,
        "properties": props,
    }
