"""Input validation for CLI arguments.

All validators raise argparse.ArgumentTypeError on invalid input.
validate_config_field / validate_profile_field map a field name + raw
string to a JSON-ready Python value.
"""

import argparse
import json
from datetime import datetime
from typing import Any, Callable, Union

from controller.config import PROFILE_DEFAULTS

VALID_PROFILE_NAMES = frozenset(PROFILE_DEFAULTS)

ValidatedValue = Union[str, int, float, bool, list[int]]

# -----------------------------------------------------------------------
# Primitive validators
# -----------------------------------------------------------------------

def _int_range(lo: int, hi: int, label: str) -> Callable[[str], int]:
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
    return _int_range(0, 359, "hue")(v)


def validate_saturation(v: str) -> int:
    return _int_range(0, 100, "saturation")(v)


def validate_brightness(v: str) -> int:
    return _int_range(0, 100, "brightness")(v)


def validate_color_temp(v: str) -> int:
    return _int_range(1200, 6500, "color_temp")(v)


def validate_positive_int(v: str) -> int:
    try:
        n = int(v)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(f"value must be a positive integer, got {v!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError(f"value must be > 0, got {n}")
    return n


def validate_time_str(v: str) -> str:
    """Validate HH:MM format; returns the canonical string (zero-padded)."""
    try:
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


validate_sparkle_transtime = _int_range(0, 200, "sparkle_transtime")
validate_sparkle_floor = _int_range(0, 100, "sparkle_floor_pct")


def validate_backoff_schedule(v: str) -> list[int]:
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


def validate_rgb_str(v: str) -> tuple[int, int, int]:
    """Parse 'R,G,B' string into an (r, g, b) tuple. Raises ArgumentTypeError on invalid input."""
    parts = v.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "color must be R,G,B (three comma-separated integers 0-255)"
        )
    try:
        r, g, b = (int(x.strip()) for x in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("color channel values must be integers")
    if not all(0 <= c <= 255 for c in (r, g, b)):
        raise argparse.ArgumentTypeError("color channel values must be 0-255")
    return r, g, b


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
# Config field dispatch — one entry per Config field, constraint explicit
# -----------------------------------------------------------------------

_CONFIG_FIELD_VALIDATORS: dict[str, Callable[[str], Any]] = {
    # Morning times
    "morning_latest_start":       validate_time_str,
    "full_morning_time":          validate_time_str,
    # Evening times
    "force_evening_time":         validate_time_str,
    "night_full_time":            validate_time_str,
    "hard_cutoff_time":           validate_time_str,
    # Weather fetch anchors
    "weather_fetch_night":        validate_time_str,
    "weather_fetch_morning":      validate_time_str,
    "weather_fetch_midday":       validate_time_str,
    "weather_fetch_evening":      validate_time_str,
    "weather_fetch_late_evening": validate_time_str,
    "weather_cache_max_age_hours": validate_positive_int,
    # Adverse weather
    "adverse_offset_min":         validate_positive_int,
    "adverse_offset_max":         validate_positive_int,
    "cloud_threshold":            _int_range(0, 100, "cloud_threshold"),
    # Darkness detection
    "dark_sun_elevation_deg":     validate_sun_elevation,
    "dark_cloud_threshold":       _int_range(0, 100, "dark_cloud_threshold"),
    # Oscillation
    "day_toggle_lockout_minutes": validate_positive_int,
    # Late-night
    "late_night_fade_minutes":    validate_positive_int,
    # Party
    "party_default_end":          validate_time_str,
    "party_default_fade_minutes": validate_positive_int,
    # Backoff
    "backoff_schedule_minutes":   validate_backoff_schedule,
    # Cron interval
    "cron_interval_minutes":      validate_positive_int,
    # Verbose
    "verbose":                    validate_bool,
    # Current guard / sparkle (Phase 1 v2)
    "current_guard_enabled":      validate_bool,
    "current_guard_threshold":    _int_range(0, 100, "current_guard_threshold"),
    "sparkle_floor_pct":          _int_range(0, 100, "sparkle_floor_pct"),
    "sparkle_transtime":          _int_range(0, 200, "sparkle_transtime"),
    "sparkle_rotation_interval":  validate_positive_int,
}


def validate_config_field(key: str, value_str: str) -> ValidatedValue:
    """Validate a raw CLI string for the given Config field key.

    Returns a JSON-ready Python value (str for time, bool, int, float, list).
    Raises argparse.ArgumentTypeError on invalid input or unknown key.
    """
    validator = _CONFIG_FIELD_VALIDATORS.get(key)
    if validator is None:
        raise argparse.ArgumentTypeError(f"unknown config key: {key!r}")
    return validator(value_str)


_PROFILE_FIELD_VALIDATORS: dict[str, Callable[[str], ValidatedValue]] = {
    "mode":        validate_profile_mode,
    "hue":         validate_hue,
    "saturation":  validate_saturation,
    "brightness":  validate_brightness,
    "color_temp":  validate_color_temp,
}


def validate_profile_field(field_name: str, value_str: str) -> ValidatedValue:
    """Validate a raw CLI string for the given LightProfile field.

    Returns a JSON-ready Python value.
    Raises argparse.ArgumentTypeError on invalid input or unknown field.
    """
    validator = _PROFILE_FIELD_VALIDATORS.get(field_name)
    if validator is None:
        valid = ", ".join(sorted(_PROFILE_FIELD_VALIDATORS))
        raise argparse.ArgumentTypeError(
            f"unknown profile field {field_name!r}; valid fields: {valid}"
        )
    return validator(value_str)
