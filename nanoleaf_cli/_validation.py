"""Input validation for CLI arguments.

All validators raise argparse.ArgumentTypeError on invalid input.
Dispatch helpers validate_config_field / validate_profile_field map
a field name + raw string to a JSON-ready Python value.
"""

import argparse
import json
from dataclasses import fields
from datetime import time

from controller.config import Config, PROFILE_DEFAULTS

VALID_PROFILE_NAMES = frozenset(PROFILE_DEFAULTS)

# -----------------------------------------------------------------------
# Primitive validators
# -----------------------------------------------------------------------

def _int_range(lo: int, hi: int, label: str):
    def _validate(v: str) -> int:
        try:
            n = int(v)
        except (ValueError, TypeError):
            raise argparse.ArgumentTypeError(f"{label} must be an integer, got {v!r}")
        if not (lo <= n <= hi):
            raise argparse.ArgumentTypeError(f"{label} must be between {lo} and {hi}, got {n}")
        return n
    _validate.__name__ = f"validate_{label.replace(' ', '_')}"
    return _validate


def validate_hue(v: str) -> int:
    return _int_range(0, 360, "hue")(v)


def validate_saturation(v: str) -> int:
    return _int_range(0, 100, "saturation")(v)


def validate_brightness(v: str) -> int:
    return _int_range(0, 100, "brightness")(v)


def validate_color_temp(v: str) -> int:
    return _int_range(1200, 6500, "color_temp")(v)


def validate_rgb_channel(v: str) -> int:
    return _int_range(0, 255, "RGB channel")(v)


def validate_percentage(v: str) -> int:
    return _int_range(0, 100, "percentage")(v)


def validate_minutes(v: str) -> int:
    try:
        n = int(v)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(f"minutes must be an integer, got {v!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError(f"minutes must be > 0, got {n}")
    return n


def validate_time_str(v: str) -> str:
    """Validate HH:MM format; returns the canonical string (zero-padded)."""
    try:
        from datetime import datetime
        t = datetime.strptime(v.strip(), "%H:%M").time()
        return t.strftime("%H:%M")
    except ValueError:
        raise argparse.ArgumentTypeError(f"time must be HH:MM (24 h), got {v!r}")


def validate_sun_elevation(v: str) -> float:
    try:
        f = float(v)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(f"sun elevation must be a number, got {v!r}")
    if not (-90.0 <= f <= 90.0):
        raise argparse.ArgumentTypeError(f"sun elevation must be between -90 and 90, got {f}")
    return f


def validate_profile_mode(v: str) -> str:
    norm = v.strip().lower()
    if norm not in ("hsb", "ct"):
        raise argparse.ArgumentTypeError(f"mode must be 'hsb' or 'ct', got {v!r}")
    return norm


def validate_bool(v: str) -> bool:
    norm = v.strip().lower()
    if norm in ("true", "yes", "1"):
        return True
    if norm in ("false", "no", "0"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false/yes/no/1/0, got {v!r}")


def validate_backoff_schedule(v: str) -> list:
    """Accept a JSON array '[5,10,20]' or comma-separated '5,10,20'."""
    v = v.strip()
    if v.startswith("["):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            raise argparse.ArgumentTypeError(f"invalid JSON array: {v!r}")
    else:
        try:
            parsed = [int(x.strip()) for x in v.split(",")]
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"expected comma-separated integers (e.g. 5,10,20), got {v!r}"
            )
    if not isinstance(parsed, list) or not parsed:
        raise argparse.ArgumentTypeError("backoff_schedule_minutes must be a non-empty list")
    for x in parsed:
        if not isinstance(x, int) or x <= 0:
            raise argparse.ArgumentTypeError(
                f"each backoff value must be a positive integer, got {x!r}"
            )
    return parsed


# -----------------------------------------------------------------------
# Profile name validator
# -----------------------------------------------------------------------

def validate_profile_name(name: str) -> str:
    norm = name.strip().upper()
    if norm not in VALID_PROFILE_NAMES:
        valid = ", ".join(sorted(VALID_PROFILE_NAMES))
        raise argparse.ArgumentTypeError(
            f"unknown profile {name!r}; valid names: {valid}"
        )
    return norm


# -----------------------------------------------------------------------
# Dispatch helpers
# -----------------------------------------------------------------------

# Config fields whose type needs special validation beyond the dataclass default
_CONFIG_FIELD_TYPES: dict[str, str] = {}

def _build_config_field_map() -> dict[str, str]:
    """Map Config field name → type tag used by validate_config_field."""
    from datetime import time as _time
    result = {}
    dummy = Config()
    for f in fields(Config):
        current = getattr(dummy, f.name)
        if isinstance(current, bool):
            result[f.name] = "bool"
        elif isinstance(current, _time):
            result[f.name] = "time"
        elif isinstance(current, float):
            result[f.name] = "float"
        elif isinstance(current, list):
            result[f.name] = "backoff"
        elif isinstance(current, int):
            result[f.name] = "int"
        else:
            result[f.name] = "str"
    return result


_CLOUD_FIELDS = {"cloud_threshold", "dark_cloud_threshold"}
_POSITIVE_INT_FIELDS = {
    "weather_cache_max_age_hours",
    "adverse_offset_min",
    "adverse_offset_max",
    "day_toggle_lockout_minutes",
    "late_night_fade_minutes",
    "party_default_fade_minutes",
}


def validate_config_field(key: str, value_str: str):
    """Validate a raw CLI string for the given Config field key.

    Returns a JSON-ready Python value (str for time, bool, int, float, list).
    Raises argparse.ArgumentTypeError on invalid input.
    """
    type_map = _build_config_field_map()
    if key not in type_map:
        raise argparse.ArgumentTypeError(f"unknown config key: {key!r}")

    tag = type_map[key]

    if tag == "bool":
        return validate_bool(value_str)
    if tag == "time":
        return validate_time_str(value_str)
    if tag == "float":
        return validate_sun_elevation(value_str)
    if tag == "backoff":
        return validate_backoff_schedule(value_str)
    if tag == "int":
        if key in _CLOUD_FIELDS:
            return _int_range(0, 100, key)(value_str)
        if key in _POSITIVE_INT_FIELDS:
            return validate_minutes(value_str)
        try:
            return int(value_str)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{key} must be an integer, got {value_str!r}")
    # fallback str
    return value_str


_PROFILE_FIELD_VALIDATORS = {
    "mode":        validate_profile_mode,
    "hue":         validate_hue,
    "saturation":  validate_saturation,
    "brightness":  validate_brightness,
    "color_temp":  validate_color_temp,
}


def validate_profile_field(field_name: str, value_str: str):
    """Validate a raw CLI string for the given LightProfile field.

    Returns a JSON-ready Python value.
    Raises argparse.ArgumentTypeError on invalid input.
    """
    validator = _PROFILE_FIELD_VALIDATORS.get(field_name)
    if validator is None:
        valid = ", ".join(sorted(_PROFILE_FIELD_VALIDATORS))
        raise argparse.ArgumentTypeError(
            f"unknown profile field {field_name!r}; valid fields: {valid}"
        )
    return validator(value_str)
