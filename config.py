"""config.py

Controller configuration: Config dataclass, LightProfile dataclass,
built-in profile constants, and the two-layer config loader.
"""

import json
import logging
from dataclasses import dataclass, field, fields
from datetime import time
from pathlib import Path

from dateTime import parse_time

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "nanoleafControlPanel" / "config.json"


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
    backoff_schedule_minutes: list = field(default_factory=lambda: [5, 10, 20, 40, 60])

    # --- Verbose logging ---
    verbose: bool = False


# ---------------------------------------------------------------------------
# Light profiles
# ---------------------------------------------------------------------------

@dataclass
class LightProfile:
    mode: str           # "hsb" or "ct"
    hue: int = 0
    saturation: int = 0
    brightness: int = 0
    color_temp: int = 0


# Sunrise start: warm dim amber (beginning of two-stage morning ramp)
SUNRISE_START_PROFILE = LightProfile(mode="hsb", hue=20, saturation=70, brightness=5)

# Sunrise end / morning ramp stage 1 target: warm bright (end of stage 1)
SUNRISE_END_PROFILE = LightProfile(mode="hsb", hue=40, saturation=20, brightness=90)

# Morning: cool blue-white, energizing (stage 2 target — final morning state)
MORNING_PROFILE = LightProfile(mode="ct", color_temp=6000, brightness=100)

# Daytime-on (used when outside is dark): amber, soft
DAYTIME_ON_PROFILE = LightProfile(mode="hsb", hue=30, saturation=50, brightness=60)

# Night: deep warm red-orange, cozy, dim
NIGHT_PROFILE = LightProfile(mode="hsb", hue=15, saturation=80, brightness=20)

# Late-night manual override: warm, low, visible
LATE_NIGHT_PROFILE = LightProfile(mode="hsb", hue=15, saturation=75, brightness=35)

# Default party profile: vivid purple, full brightness
PARTY_PROFILE = LightProfile(mode="hsb", hue=280, saturation=90, brightness=100)

# Off target (brightness=0 signals power-off intent to interpolate_profiles)
OFF_PROFILE = LightProfile(mode="hsb", brightness=0)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config() -> Config:
    """Return Config built from defaults overlaid with ~/.config/nanoleafControlPanel/config.json.

    Unknown keys (e.g. color_names used by describe_color) are silently ignored here
    and read directly from the file by the functions that need them.
    Missing file or parse errors fall back to defaults.
    """
    config = Config()
    if not CONFIG_PATH.exists():
        return config

    try:
        with open(CONFIG_PATH) as f:
            overrides = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("load_config: could not read %s (%s) — using defaults", CONFIG_PATH, exc)
        return config

    valid_keys = {f.name for f in fields(Config)}
    for key, value in overrides.items():
        if key not in valid_keys:
            continue
        try:
            current = getattr(config, key)
            if isinstance(current, time):
                value = parse_time(value)
            setattr(config, key, value)
        except (ValueError, TypeError) as exc:
            logger.warning("load_config: invalid value for %s (%s) — keeping default", key, exc)

    return config
