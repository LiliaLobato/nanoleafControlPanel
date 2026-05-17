"""color_helper.py

describe_color() helper: translates a LightProfile into a human-readable
description used by CLI messages and verbose controller logs.
"""

import logging

from config import CONFIG_PATH, LightProfile, read_json_cached

logger = logging.getLogger(__name__)

# Default lookup tables: (inclusive_start, inclusive_end, name)
_HUE_DEFAULTS = [
    (0,   10,  "red"),
    (11,  25,  "amber"),
    (26,  45,  "orange"),
    (46,  65,  "yellow"),
    (66,  80,  "shrek green"),
    (81,  160, "green"),
    (161, 200, "cyan"),
    (201, 245, "blue"),
    (246, 280, "purple"),
    (281, 320, "magenta"),
    (321, 345, "pink"),
    (346, 360, "red"),
]

_SAT_DEFAULTS = [
    (0,  10,  "near white"),
    (11, 35,  "light"),
    (36, 65,  "soft"),
    (66, 85,  "vivid"),
    (86, 100, "deep"),
]

_BRIGHTNESS_DEFAULTS = [
    (0,  0,   "off"),
    (1,  15,  "very dim"),
    (16, 35,  "dim"),
    (36, 65,  "moderate"),
    (66, 85,  "bright"),
    (86, 100, "full"),
]

_CT_DEFAULTS = [
    (1200, 2000, "warm amber white"),
    (2001, 3000, "warm white"),
    (3001, 4500, "neutral white"),
    (4501, 5500, "cool white"),
    (5501, 6500, "daylight white"),
]


_overrides_cache: dict = {}


def _load_overrides() -> dict:
    """Return color/saturation/brightness name overrides from config.json, mtime-cached."""
    data = read_json_cached(CONFIG_PATH, _overrides_cache, logger)
    return {
        "color_names":      data.get("color_names", {}),
        "saturation_names": data.get("saturation_names", {}),
        "brightness_names": data.get("brightness_names", {}),
    }


def _lookup(value: int, defaults: list, overrides: dict) -> str:
    """Return the name for a value, with config overrides taking precedence.

    When multiple ranges match, the most specific (narrowest) range wins.
    Override keys are "start-end" strings (e.g. "15-20").
    """
    candidates = []

    for start, end, name in defaults:
        if start <= value <= end:
            candidates.append((end - start, name))

    for range_str, name in overrides.items():
        try:
            lo, hi = (int(x) for x in range_str.split("-"))
            if lo <= value <= hi:
                candidates.append((hi - lo, name))
        except (ValueError, AttributeError):
            logger.warning("color_helper: invalid range key %r — skipping", range_str)

    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def describe_color(profile: LightProfile) -> str:
    """Return a human-readable description of a LightProfile.

    Examples:
      HSB(280, 90, 100) → "deep purple color, full brightness"
      HSB(15, 80, 20)   → "vivid amber color, dim brightness"
      CT(6000, 100)     → "daylight white, full brightness"
      HSB(0, 0, 0)      → "off"
    """
    if profile.brightness == 0:
        return "off"

    overrides = _load_overrides()
    brightness_desc = _lookup(
        profile.brightness, _BRIGHTNESS_DEFAULTS, overrides.get("brightness_names", {})
    )

    if profile.mode == "ct":
        ct_name = _lookup(profile.color_temp, _CT_DEFAULTS, {})
        return f"{ct_name}, {brightness_desc} brightness"

    hue_name = _lookup(
        profile.hue, _HUE_DEFAULTS, overrides.get("color_names", {})
    )
    sat_mod = _lookup(
        profile.saturation, _SAT_DEFAULTS, overrides.get("saturation_names", {})
    )
    return f"{sat_mod} {hue_name} color, {brightness_desc} brightness"
