"""Canonical HA flag / label taxonomy and display-timezone resolution.

Single source of truth shared by the Python pipeline and the SPA:

  * the Home Assistant vision flag field names (boolean + string) the SPA
    renders as badges and must never mistake for object identity,
  * the parent-boolean rules and sentinel values for string flags,
  * the flags dropped from the live deep-pass path,
  * the detector presence keys and gate-ignore labels used by catalog.py,
  * the display timezone default and ``WEBCAM_TZ`` > settings resolution.

Import-light: no cv2 and no analyze_images, so catalog.py / scans.py and the
SPA's ``/api/taxonomy`` endpoint can load it cheaply.

The SPA consumes ``payload()`` via ``GET /api/taxonomy``. index.html keeps an
embedded fallback for offline first paint; tests/test_taxonomy_parity.js fails
if that fallback drifts from this module.
"""

# Detector presence keys: the YOLO classes that are object identity (labels),
# never HA flags. catalog.py imports these.
YOLO_PRESENCE_KEYS = ("person", "dog", "car", "cat", "bird", "face", "body")
GATE_IGNORE_LABELS = ("car",)

# HA vision flags from the front/back deep-pass JSON schemas. Order matches
# analyze_images.LLM_ACTIVITY_KEYS (front required, then back-only extras).
HA_FLAG_KEYS = (
    "postal_delivery", "postal_how", "dog_walked", "car_access",
    "enters_car", "exits_car", "car_outfit", "car_color", "car_make",
    "opens_box", "porch_access", "animal_detected", "animal_type",
    "approaching_house", "leaving_house", "weapon_detected", "clothes_drying",
)

# String-valued flags render only when at least one parent boolean is true.
HA_STRING_PARENTS = {
    "postal_how": ("postal_delivery",),
    "animal_type": ("animal_detected",),
    "car_outfit": ("enters_car", "exits_car"),
    "car_color": ("car_access", "enters_car", "exits_car"),
    "car_make": ("car_access", "enters_car", "exits_car"),
}

# Lowercased string values that mean "no value" and must not render a badge.
HA_STRING_SENTINELS = ("", "none", "false", "unknown")

# Dropped from the live path (vision audit / never-true).
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

# Display timezone fallback. WEBCAM_TZ env > settings.json `timezone` > default.
DEFAULT_TZ = "Australia/Sydney"


def resolve_timezone(env_val=None, settings_val=None):
    """Display timezone resolution: WEBCAM_TZ env > settings.json `timezone`
    > DEFAULT_TZ. Blank/whitespace values are ignored."""
    for v in (env_val, settings_val):
        if isinstance(v, str) and v.strip():
            return v.strip()
    return DEFAULT_TZ


def payload():
    """JSON-serialisable taxonomy for the SPA (``GET /api/taxonomy``)."""
    return {
        "ha_flag_keys": list(HA_FLAG_KEYS),
        "ha_string_parents": {k: list(v) for k, v in HA_STRING_PARENTS.items()},
        "ha_string_sentinels": list(HA_STRING_SENTINELS),
        "dropped_flags": list(DROPPED_FLAGS),
        "default_tz": DEFAULT_TZ,
    }
