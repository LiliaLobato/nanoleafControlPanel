"""state.py

Persistent state management for the Nanoleaf controller:
state.json load/save (atomic) and the cron-overlap file lock.
"""

import json
import logging
import os
from pathlib import Path

import filelock

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
