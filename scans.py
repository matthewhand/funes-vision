"""Priority one-question e2b scans.

The full front/back HA schemas are too many questions for e2b in one
call (and several flags are systematic false positives). Each scan is a
tiny JSON schema. YOLO gates decide which run; order is priority.
Does not import analyze_images (cv2).
"""

# Default: at most two vision calls per frame (~40s each).
MAX_SCANS_PER_IMAGE = 2
SCAN_TOKENS = 48

# Dropped from the live path (vision audit / never-true):
# approaching_house, leaving_house, car_access, enters_car, exits_car,
# car_outfit, car_color, car_make, clothes_drying, weapon_detected.

_POSTAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["postal_delivery", "postal_how"],
    "properties": {
        "postal_delivery": {
            "type": "boolean",
            "description": (
                "True only if a postie, courier, or parcel delivery is "
                "happening now. A resident, walker, or suitcase is false."
            ),
        },
        "postal_how": {
            "type": "string",
            "enum": ["van", "bike", "on foot", "truck", "scooter", "car",
                     "unknown", "none"],
            "description": "How the delivery arrives. none if postal_delivery is false.",
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
                "True only if a person is on or stepping onto the front "
                "porch/entrance. A person on the path or driveway is false."
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

_ANIMAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["animal_detected", "animal_type"],
    "properties": {
        "animal_detected": {
            "type": "boolean",
            "description": "True only if a live animal is visible in this frame.",
        },
        "animal_type": {
            "type": "string",
            "enum": ["dog", "cat", "bird", "wildlife", "other", "none"],
            "description": "none if animal_detected is false.",
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
                "True only if a person is walking a dog (leash or clearly "
                "accompanying). A dog alone is false."
            ),
        },
    },
}

# Priority order. First matching scans run up to MAX_SCANS_PER_IMAGE.
SCANS = (
    {
        "id": "postal",
        "cameras": ("front",),
        "need_any": ("person", "face", "body"),
        "schema": _POSTAL,
        "num_predict": SCAN_TOKENS,
    },
    {
        "id": "porch",
        "cameras": ("front",),
        "need_any": ("person", "face", "body"),
        "schema": _PORCH,
        "num_predict": 32,
    },
    {
        "id": "animal",
        "cameras": ("front", "back"),
        "need_any": ("dog", "cat", "bird"),
        "schema": _ANIMAL,
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
        "id": "package",
        "cameras": ("front",),
        "need_any": ("person", "face", "body"),
        "schema": _PACKAGE,
        "num_predict": 32,
    },
)

DROPPED_FLAGS = (
    "approaching_house",
    "leaving_house",
    "car_access",
    "enters_car",
    "exits_car",
    "car_outfit",
    "car_color",
    "car_make",
    "clothes_drying",
    "weapon_detected",
)


def yolo_set(fp_labels):
    if isinstance(fp_labels, dict):
        return {k for k, v in fp_labels.items() if v is True}
    return {k for k in (fp_labels or []) if k}


def scans_for(kind, fp_labels, limit=MAX_SCANS_PER_IMAGE):
    """Ordered applicable scans for this camera + detector labels."""
    ys = yolo_set(fp_labels)
    out = []
    for spec in SCANS:
        if kind not in spec["cameras"]:
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


def union_schema(specs):
    props, req = {}, []
    for spec in specs:
        sch = spec.get("schema") or {}
        props.update(sch.get("properties") or {})
        for k in sch.get("required") or []:
            if k not in req:
                req.append(k)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": req,
        "properties": props,
    }
