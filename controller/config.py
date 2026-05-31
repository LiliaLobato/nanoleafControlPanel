"""config.py

Controller configuration: Config dataclass, LightProfile dataclass,
built-in profile constants, and the two-layer config loader.
"""

import json
import logging
import os
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path
from typing import Any, Literal

from controller.dateTime import parse_time

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "nanoleafControlPanel" / "config.json"


def read_json_cached(path: Path, cache: dict, log: Any = None) -> dict:
    """Read a JSON file with mtime-based caching.

    On each call, checks the file's modification time. Returns the cached
    dict if the file is unchanged; re-reads only when it has been modified.
    Falls back to the last good cached data on read errors, so callers never
    receive an empty dict due to a transient I/O failure.
    """
    if not path.exists():
        return cache.get("data", {})
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return cache.get("data", {})
    if mtime == cache.get("mtime"):
        return cache["data"]
    try:
        with open(path) as f:
            data = json.load(f)
        cache["mtime"] = mtime
        cache["data"] = data
        return data
    except (json.JSONDecodeError, OSError) as exc:
        if log:
            log.warning("read_json_cached: could not read %s (%s)", path, exc)
        return cache.get("data", {})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    # --- Morning ---
    morning_latest_start: time = field(default_factory=lambda: time(6, 0))
    full_morning_time: time = field(default_factory=lambda: time(7, 0))

    # --- Evening ---
    force_evening_time: time = field(default_factory=lambda: time(21, 0))
    night_full_time: time = field(default_factory=lambda: time(22, 0))
    hard_cutoff_time: time = field(default_factory=lambda: time(23, 0))

    # --- Weather fetch anchors (5x/day) ---
    weather_fetch_night: time = field(default_factory=lambda: time(0, 0))
    weather_fetch_morning: time = field(default_factory=lambda: time(3, 0))
    weather_fetch_midday: time = field(default_factory=lambda: time(9, 0))
    weather_fetch_evening: time = field(default_factory=lambda: time(14, 0))
    weather_fetch_late_evening: time = field(default_factory=lambda: time(20, 0))
    weather_cache_max_age_hours: int = 5

    # --- Adverse weather sunset offset (scales with cloud cover) ---
    adverse_offset_min: int = 30
    adverse_offset_max: int = 75
    cloud_threshold: int = 60

    # --- Darkness detection ---
    dark_sun_elevation_deg: float = 20.0
    dark_cloud_threshold: int = 75

    # --- Oscillation protection ---
    day_toggle_lockout_minutes: int = 30

    # --- Late-night manual override ---
    late_night_fade_minutes: int = 120

    # --- Party mode defaults ---
    party_default_end: time = field(default_factory=lambda: time(2, 0))
    party_default_fade_minutes: int = 30

    # --- Failure backoff ---
    backoff_schedule_minutes: list[int] = field(default_factory=lambda: [5, 10, 20, 40, 60])

    # --- Cron tick interval ---
    # Must match the */N in the crontab entry. Controls the anchor-time window
    # width in weather_cache._is_anchor_time so exactly one tick fires per anchor.
    cron_interval_minutes: int = 2

    # --- Verbose logging ---
    verbose: bool = False


# ---------------------------------------------------------------------------
# Light profiles
# ---------------------------------------------------------------------------

@dataclass
class LightProfile:
    mode: Literal["hsb", "ct"]
    hue: int = 0
    saturation: int = 0
    brightness: int = 0
    color_temp: int = 0


# Sunrise start: warm dim amber (beginning of two-stage morning ramp)
SUNRISE_START_PROFILE = LightProfile(mode="hsb", hue=20, saturation=70, brightness=5)

# Sunrise end / morning ramp stage 1 target: warm bright (end of stage 1)
SUNRISE_END_PROFILE = LightProfile(mode="hsb", hue=40, saturation=20, brightness=50)

# Morning: cool blue-white, energizing (stage 2 target — final morning state)
MORNING_PROFILE = LightProfile(mode="ct", color_temp=6000, brightness=55)

# Daytime-on (used when outside is dark): warm orange-red, soft
DAYTIME_ON_PROFILE = LightProfile(mode="hsb", hue=15, saturation=80, brightness=33)

# Night: deep red, cozy, dim
NIGHT_PROFILE = LightProfile(mode="hsb", hue=8, saturation=90, brightness=20)

# Late-night manual override: pure red, low, visible
LATE_NIGHT_PROFILE = LightProfile(mode="hsb", hue=4, saturation=90, brightness=25)

# Default party profile: vivid purple, full brightness
PARTY_PROFILE = LightProfile(mode="hsb", hue=280, saturation=90, brightness=55)

# Off target (brightness=0 signals power-off intent to interpolate_profiles)
OFF_PROFILE = LightProfile(mode="hsb", brightness=0)

# Canonical name → default constant mapping used by load_profiles() and the CLI.
PROFILE_DEFAULTS: dict[str, LightProfile] = {
    "SUNRISE_START": SUNRISE_START_PROFILE,
    "SUNRISE_END":   SUNRISE_END_PROFILE,
    "MORNING":       MORNING_PROFILE,
    "DAYTIME_ON":    DAYTIME_ON_PROFILE,
    "NIGHT":         NIGHT_PROFILE,
    "LATE_NIGHT":    LATE_NIGHT_PROFILE,
    "PARTY":         PARTY_PROFILE,
    "OFF":           OFF_PROFILE,
}


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_config_cache: dict = {}


def load_config() -> Config:
    """Return Config built from defaults overlaid with ~/.config/nanoleafControlPanel/config.json.

    Uses mtime-based caching — re-reads the file only when it has been modified.
    Unknown keys (e.g. color_names/saturation_names used by describe_color in color_helper) are silently ignored here
    and read directly from the file by the functions that need them.
    Missing file or parse errors fall back to defaults.
    """
    config = Config()
    overrides = read_json_cached(CONFIG_PATH, _config_cache, logger)
    if not overrides:
        return config

    valid_keys = {f.name for f in fields(Config)}
    for key, value in overrides.items():
        if key not in valid_keys:
            continue
        try:
            current = getattr(config, key)
            # bool must be checked before int — bool is a subclass of int
            if isinstance(current, bool):
                if not isinstance(value, bool):
                    raise TypeError(f"expected bool, got {type(value).__name__!r}")
            elif isinstance(current, time):
                value = parse_time(value)
            elif isinstance(current, int):
                if isinstance(value, float):
                    logger.warning(
                        "load_config: %r expects int, got float %.4g — truncating to %d",
                        key, value, int(value),
                    )
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            elif isinstance(current, list):
                if not isinstance(value, list):
                    raise TypeError(f"expected list, got {type(value).__name__!r}")
                if not all(isinstance(x, int) for x in value):
                    raise TypeError("expected list[int], got non-int element")
            setattr(config, key, value)
        except (ValueError, TypeError) as exc:
            logger.warning("load_config: invalid value for %r (%s) — keeping default", key, exc)

    # Cross-field time-ordering checks: inverted times create unreachable phases.
    if config.full_morning_time <= config.morning_latest_start:
        logger.warning(
            "load_config: full_morning_time (%s) must be after morning_latest_start (%s) "
            "— morning_ramp phase will be unreachable",
            config.full_morning_time, config.morning_latest_start,
        )
    if config.night_full_time <= config.force_evening_time:
        logger.warning(
            "load_config: night_full_time (%s) must be after force_evening_time (%s) "
            "— night_ramp phase will be unreachable",
            config.night_full_time, config.force_evening_time,
        )
    if config.hard_cutoff_time <= config.night_full_time:
        logger.warning(
            "load_config: hard_cutoff_time (%s) must be after night_full_time (%s) "
            "— hard_cutoff_ramp phase will be unreachable",
            config.hard_cutoff_time, config.night_full_time,
        )

    return config


def save_config(data: dict) -> None:
    """Atomically write data to CONFIG_PATH via temp file + os.replace().

    Creates the config directory if it doesn't exist.
    The caller is responsible for reading, merging, and validating before calling.
    Clears the mtime cache so the very next load_config() / load_profiles() call
    reads the file fresh instead of returning the now-stale cached data.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.parent / (CONFIG_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    _config_cache.clear()


def load_profiles() -> dict[str, LightProfile]:
    """Return effective profiles: default constants merged with config.json overrides.

    Uses the same mtime-cached config.json read as load_config() — one file
    stat per call, re-reads only when config.json changes.
    Profiles absent from config.json return their default constant unchanged.
    Invalid stored values fall back to the default constant.
    """
    overrides = read_json_cached(CONFIG_PATH, _config_cache, logger)
    profile_overrides = overrides.get("profiles", {})
    if not profile_overrides:
        return dict(PROFILE_DEFAULTS)

    result: dict[str, LightProfile] = {}
    for name, default in PROFILE_DEFAULTS.items():
        stored = profile_overrides.get(name)
        if not stored:
            result[name] = default
            continue
        merged = {
            "mode":       stored.get("mode",       default.mode),
            "hue":        stored.get("hue",        default.hue),
            "saturation": stored.get("saturation", default.saturation),
            "brightness": stored.get("brightness", default.brightness),
            "color_temp": stored.get("color_temp", default.color_temp),
        }
        try:
            result[name] = LightProfile(**merged)
        except (TypeError, ValueError) as exc:
            logger.warning("load_profiles: invalid override for %r (%s) — using default", name, exc)
            result[name] = default
    return result
