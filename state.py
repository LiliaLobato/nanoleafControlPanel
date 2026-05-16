"""state.py

Persistent state management for the Nanoleaf controller:
state.json load/save (atomic), cron-overlap file lock, and DND management.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import filelock

from config import Config
from dateTime import combine

logger = logging.getLogger(__name__)

STATE_DIR  = Path.home() / ".local" / "share" / "nanoleafControlPanel"
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH  = STATE_DIR / "controller.lock"


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


# ---------------------------------------------------------------------------
# DND management (Task 12)
# ---------------------------------------------------------------------------

def apply_dnd_flag(state: dict, phase: str, now: datetime, config: Config) -> None:
    """Set the DND flag after a manual_off override is detected.

    morning_ramp scope: clears at full_morning_time today.
    overnight scope:    clears at next morning ramp start (evening/night phases).
    Day and late-night phases do not trigger DND.
    """
    if phase == "morning_ramp":
        state["do_not_disturb_until"] = combine(now, config.full_morning_time).isoformat()
        state["dnd_scope"] = "morning_ramp"
    elif phase in ("evening_ramp", "night_ramp", "hard_cutoff_ramp"):
        tomorrow = now.date() + timedelta(days=1)
        dnd_until = datetime.combine(tomorrow, config.full_morning_time).replace(tzinfo=now.tzinfo)
        state["do_not_disturb_until"] = dnd_until.isoformat()
        state["dnd_scope"] = "overnight"


def should_respect_dnd(state: dict, now: datetime) -> bool:
    """Return True if DND is currently active and has not yet expired."""
    dnd_until = state.get("do_not_disturb_until")
    if not dnd_until:
        return False
    return datetime.fromisoformat(dnd_until) > now


def clear_dnd_if_expired(
    state: dict,
    now: datetime,
    config: Config,
    weather: Optional[object] = None,
) -> None:
    """Clear DND when its scope condition is met (time-based, not phase-based).

    morning_ramp scope: clears when now >= full_morning_time today.
    overnight scope:    clears when now >= min(sunrise, morning_latest_start).
    """
    scope = state.get("dnd_scope")
    if not scope:
        return

    if scope == "morning_ramp":
        if now >= combine(now, config.full_morning_time):
            state["do_not_disturb_until"] = None
            state["dnd_scope"] = None

    elif scope == "overnight":
        morning_latest = combine(now, config.morning_latest_start)
        if weather:
            morning_ramp_start = min(weather.get_sunrise_dt(), morning_latest)
        else:
            morning_ramp_start = morning_latest
        if now >= morning_ramp_start:
            state["do_not_disturb_until"] = None
            state["dnd_scope"] = None


# ---------------------------------------------------------------------------
# Lamp failure backoff (Task 13)
# ---------------------------------------------------------------------------

def is_lamp_in_backoff(state: dict, now: datetime) -> bool:
    """Return True if the lamp is in backoff and API calls should be skipped."""
    next_retry = state.get("lamp_failure_state", {}).get("next_retry_at")
    if not next_retry:
        return False
    return datetime.fromisoformat(next_retry) > now


def handle_lamp_success(state: dict) -> None:
    """Reset lamp failure state after a successful API call."""
    failure = state["lamp_failure_state"]
    if failure["consecutive_failures"] > 0:
        logger.info(
            "Lamp recovered after %d consecutive failures",
            failure["consecutive_failures"],
        )
    failure["consecutive_failures"] = 0
    failure["last_failure_at"] = None
    failure["last_failure_type"] = None
    failure["next_retry_at"] = None


def handle_lamp_failure(state: dict, now: datetime, config: Config, exc: Exception) -> None:
    """Increment lamp failure state and schedule next retry with exponential backoff."""
    failure = state["lamp_failure_state"]
    failure["consecutive_failures"] += 1
    failure["last_failure_at"] = now.isoformat()
    failure["last_failure_type"] = type(exc).__name__
    n = failure["consecutive_failures"]
    backoff_min = config.backoff_schedule_minutes[
        min(n - 1, len(config.backoff_schedule_minutes) - 1)
    ]
    retry_at = now + timedelta(minutes=backoff_min)
    failure["next_retry_at"] = retry_at.isoformat()
    state["last_error"] = {
        "timestamp": now.isoformat(),
        "type": type(exc).__name__,
        "message": str(exc),
    }
    logger.warning(
        "Lamp API failure %d/%d, backing off until %s (%s)",
        n, len(config.backoff_schedule_minutes),
        retry_at.strftime("%H:%M"), exc,
    )


# ---------------------------------------------------------------------------
# Manual override detection
# ---------------------------------------------------------------------------

def detect_manual_override(
    light_state: dict,
    last_applied: dict,
    phase: str,
) -> str:
    """Detect whether the user manually changed power since the last cron run.

    Compares actual lamp power (light_state["on"]) against the expected power
    from our last applied state (last_applied["power"]).

    Returns one of:
      "none"               — no change detected
      "manual_on"          — user turned ON when we expected OFF
      "manual_off"         — user turned OFF when we expected ON
      "late_night_trigger" — user turned ON after hard cutoff (off phase)
    """
    if not last_applied:
        return "none"

    actual_on = light_state.get("on", False)
    expected_on = last_applied.get("power", False)

    if actual_on == expected_on:
        return "none"

    if actual_on and not expected_on:
        if phase == "off":
            return "late_night_trigger"
        return "manual_on"

    return "manual_off"
