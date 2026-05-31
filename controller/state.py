"""state.py

Persistent state management for the Nanoleaf controller:
state.json load/save (atomic), cron-overlap file lock, and DND management.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import filelock

from controller.config import Config
from controller.dateTime import combine, parse_iso

logger = logging.getLogger(__name__)

STATE_DIR        = Path.home() / ".local" / "share" / "nanoleafControlPanel"
STATE_PATH       = STATE_DIR / "state.json"
LOCK_PATH        = STATE_DIR / "controller.lock"
PREVIEW_LOCK_PATH = STATE_DIR / "preview.lock"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


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
    _ensure_state_dir()
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("load_state: could not read %s (%s) — starting fresh", STATE_PATH, exc)
        return _empty_state()

    # Schema migration: ensure nested dicts added in later versions exist so
    # callers can do direct key access without KeyError on old state files.
    state.setdefault("lamp_failure_state", {})
    lfs = state["lamp_failure_state"]
    lfs.setdefault("consecutive_failures", 0)
    lfs.setdefault("last_failure_at", None)
    lfs.setdefault("last_failure_type", None)
    lfs.setdefault("next_retry_at", None)

    state.setdefault("weather_failure_state", {})
    wfs = state["weather_failure_state"]
    wfs.setdefault("consecutive_failures", 0)
    wfs.setdefault("last_failure_at", None)
    wfs.setdefault("next_retry_at", None)

    state.setdefault("party_mode", {"active": False})
    return state


def _strict_json_default(obj: object) -> None:
    raise TypeError(
        f"save_state: non-serializable value of type {type(obj).__name__!r} — "
        "call .isoformat() on datetimes, dataclasses.asdict() on dataclasses"
    )


def save_state(state: dict) -> None:
    """Atomically write state to disk via a temp file + os.replace()."""
    _ensure_state_dir()
    tmp = STATE_PATH.parent / (STATE_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=_strict_json_default)
    for attempt in range(3):
        try:
            os.replace(tmp, STATE_PATH)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.01)


# ---------------------------------------------------------------------------
# Cron overlap lock
# ---------------------------------------------------------------------------

def get_run_lock() -> filelock.FileLock:
    """Return the single-instance run lock (not yet acquired).

    Use as a context manager: ``with get_run_lock(): ...``
    The context manager calls acquire(timeout=0), raising filelock.Timeout
    immediately if another instance is already running.
    """
    _ensure_state_dir()
    return filelock.FileLock(str(LOCK_PATH), timeout=0)


# Backwards-compatible alias — prefer get_run_lock() in new code.
acquire_run_lock = get_run_lock


def get_preview_lock() -> filelock.FileLock:
    """Return the preview lock (not yet acquired).

    Acquired by CLI preview commands; the controller checks it before applying
    lamp changes and skips the tick if a preview session is active.
    """
    _ensure_state_dir()
    return filelock.FileLock(str(PREVIEW_LOCK_PATH), timeout=0)


# ---------------------------------------------------------------------------
# DND management
# ---------------------------------------------------------------------------

def apply_dnd_flag(state: dict, phase: str, now: datetime, config: Config) -> None:
    """Set the DND flag after a manual_off override is detected.

    morning_ramp scope: clears at full_morning_time today.
    overnight scope:    clears at next morning ramp start (evening/night phases).
    day phase:          intentional no-op — oscillation lockout handles re-evaluation.
    off/pre_morning:    intentional no-op — lamp is already supposed to be off.
    party_mode / late_night_override: handled separately by _run() before this is called.
    """
    if phase == "morning_ramp":
        state["do_not_disturb_until"] = combine(now, config.full_morning_time).isoformat()
        state["dnd_scope"] = "morning_ramp"
    elif phase in ("evening_ramp", "night_ramp", "hard_cutoff_ramp"):
        tomorrow = now.date() + timedelta(days=1)
        dnd_until = datetime.combine(tomorrow, config.full_morning_time).replace(tzinfo=now.tzinfo)
        state["do_not_disturb_until"] = dnd_until.isoformat()
        state["dnd_scope"] = "overnight"
    # All other phases (day, off, pre_morning, party_mode, late_night_override):
    # no DND is set — see docstring for rationale.


def should_respect_dnd(state: dict, now: datetime) -> bool:
    """Return True if DND is currently active and has not yet expired."""
    dnd_until = state.get("do_not_disturb_until")
    if not dnd_until:
        return False
    return parse_iso(dnd_until) > now


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
            morning_ramp_start = min(weather.get_sunrise_dt(tz=now.tzinfo), morning_latest)
        else:
            morning_ramp_start = morning_latest
        if now >= morning_ramp_start:
            state["do_not_disturb_until"] = None
            state["dnd_scope"] = None


# ---------------------------------------------------------------------------
# Lamp failure backoff
# ---------------------------------------------------------------------------

def is_lamp_in_backoff(state: dict, now: datetime) -> bool:
    """Return True if the lamp is in backoff and API calls should be skipped."""
    next_retry = state.get("lamp_failure_state", {}).get("next_retry_at")
    if not next_retry:
        return False
    return parse_iso(next_retry) > now


def handle_lamp_success(state: dict) -> None:
    """Reset lamp failure state after a successful API call."""
    failure = state.get("lamp_failure_state")
    if not failure:
        return
    if failure["consecutive_failures"] > 0:
        logger.info(
            "Lamp recovered after %d consecutive failures",
            failure["consecutive_failures"],
        )
    failure["consecutive_failures"] = 0
    failure["last_failure_at"] = None
    failure["last_failure_type"] = None
    failure["next_retry_at"] = None
    state["last_error"] = None


def handle_lamp_failure(state: dict, now: datetime, config: Config, exc: Exception) -> None:
    """Increment lamp failure state and schedule next retry with exponential backoff."""
    failure = state["lamp_failure_state"]
    failure["consecutive_failures"] += 1
    failure["last_failure_at"] = now.isoformat()
    failure["last_failure_type"] = type(exc).__name__
    n = failure["consecutive_failures"]
    schedule = config.backoff_schedule_minutes or [5]
    backoff_min = schedule[min(n - 1, len(schedule) - 1)]
    retry_at = now + timedelta(minutes=backoff_min)
    failure["next_retry_at"] = retry_at.isoformat()
    state["last_error"] = {
        "timestamp": now.isoformat(),
        "type": type(exc).__name__,
        "message": str(exc),
    }
    schedule_len = len(schedule)
    failure_tag = f"{n}/{schedule_len}" if n <= schedule_len else f"{n} (max backoff)"
    logger.warning(
        "Lamp API failure %s, backing off until %s (%s)",
        failure_tag, retry_at.strftime("%H:%M"), exc,
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
