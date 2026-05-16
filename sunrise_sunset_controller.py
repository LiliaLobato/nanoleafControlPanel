"""sunrise_sunset_controller.py

Cron-driven controller that runs every 5 minutes and applies the correct
Nanoleaf light state based on sunrise/sunset, weather, and manual overrides.

Usage (crontab):
    */5 * * * * /usr/bin/python3 /home/pi/nanoleafControlPanel/sunrise_sunset_controller.py
"""

import json
import logging
import os
from dataclasses import dataclass, field, fields
from datetime import datetime, time
from pathlib import Path

import filelock

logger = logging.getLogger(__name__)

# XDG-style paths — resolve to standard Linux locations on the Pi,
# and to equivalent directories under the user home on Windows for development.
CONFIG_PATH = Path.home() / ".config" / "nanoleafControlPanel" / "config.json"
STATE_DIR   = Path.home() / ".local" / "share" / "nanoleafControlPanel"
STATE_PATH  = STATE_DIR / "state.json"
LOG_DIR     = Path.home() / ".local" / "state" / "nanoleafControlPanel"
LOCK_PATH   = STATE_DIR / "controller.lock"


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

def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


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
                value = _parse_time(value)
            setattr(config, key, value)
        except (ValueError, TypeError) as exc:
            logger.warning("load_config: invalid value for %s (%s) — keeping default", key, exc)

    return config


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def _empty_state() -> dict:
    return {
        "weather_cache": None,
        "last_applied": None,
        "last_daytime_toggle_at": None,
        "do_not_disturb_until": None,
        "dnd_scope": None,
        "late_night_override": None,
        "party_mode": {"active": False},
        "lamp_failure_state": {
            "consecutive_failures": 0,
            "last_failure_at": None,
            "last_failure_type": None,
            "next_retry_at": None,
        },
        "weather_failure_state": {
            "consecutive_failures": 0,
            "last_failure_at": None,
            "next_retry_at": None,
        },
        "last_error": None,
    }


def load_state() -> dict:
    """Load state.json, returning a fresh empty state if the file is missing or corrupt.

    Also ensures STATE_DIR exists so the rest of the controller can write freely.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("load_state: could not read %s (%s) — starting fresh", STATE_PATH, exc)
        return _empty_state()


def save_state(state: dict) -> None:
    """Atomically write state to disk via a temp file + os.replace()."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.parent / (STATE_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------------------
# Cron overlap lock
# ---------------------------------------------------------------------------

def acquire_run_lock() -> filelock.FileLock:
    """Acquire the single-instance run lock.

    Returns the held lock on success. Raises filelock.Timeout immediately if
    another instance of the controller is already running, so the caller can
    exit silently without waiting.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(LOCK_PATH), timeout=0)
    lock.acquire()
    return lock
