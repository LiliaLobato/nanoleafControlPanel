"""sunrise_sunset_controller.py

Cron-driven controller that runs every 5 minutes and applies the correct
Nanoleaf light state based on sunrise/sunset, weather, and manual overrides.

Usage (crontab):
    */5 * * * * /usr/bin/python3 /home/pi/nanoleafControlPanel/sunrise_sunset_controller.py
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from config import (
    Config,
    LightProfile,
    SUNRISE_START_PROFILE,
    SUNRISE_END_PROFILE,
    MORNING_PROFILE,
    DAYTIME_ON_PROFILE,
    NIGHT_PROFILE,
    LATE_NIGHT_PROFILE,
    PARTY_PROFILE,
    OFF_PROFILE,
    load_config,
)
from dateTime import combine
from openWeather import OpenWeatherLight
from state import load_state, save_state, acquire_run_lock
from weather_cache import get_weather

load_dotenv()

logger = logging.getLogger(__name__)

LOG_DIR = Path.home() / ".local" / "state" / "nanoleafControlPanel"


# ---------------------------------------------------------------------------
# Phase calculation
# ---------------------------------------------------------------------------

def calculate_phase(
    now: datetime,
    weather: Optional[OpenWeatherLight],
    config: Config,
    state: dict,
) -> str:
    """Return the current controller phase name.

    Priority (first match wins):
    1. morning_ramp  — non-negotiable sunrise simulator
    2. party_mode    — active and not yet expired
    3. Standard timeline: pre_morning, day, evening_ramp, night_ramp,
       hard_cutoff_ramp, off
    4. late_night_override — post hard_cutoff with active manual override

    Phase boundaries:
        pre_morning      → before morning_ramp_start
        morning_ramp     → [morning_ramp_start, full_morning_time)
        day              → [full_morning_time, adjusted_sunset)
        evening_ramp     → [adjusted_sunset, force_evening_time)   DAYTIME_ON held
        night_ramp       → [force_evening_time, night_full_time)   DAYTIME_ON→NIGHT
        hard_cutoff_ramp → [night_full_time, hard_cutoff_time)     NIGHT→OFF
        off / late_night_override → [hard_cutoff_time, ...)
    """
    if weather:
        sunrise_dt = weather.get_sunrise_dt()
        morning_ramp_start = min(
            sunrise_dt,
            combine(now, config.morning_latest_start),
        )
        adjusted_sunset = weather.get_adjusted_sunset(
            config.cloud_threshold,
            config.adverse_offset_min,
            config.adverse_offset_max,
        )
    else:
        morning_ramp_start = combine(now, config.morning_latest_start)
        adjusted_sunset = combine(now, config.force_evening_time)

    full_morning_dt  = combine(now, config.full_morning_time)
    force_evening_dt = combine(now, config.force_evening_time)
    night_full_dt    = combine(now, config.night_full_time)
    hard_cutoff_dt   = combine(now, config.hard_cutoff_time)

    # 1. Morning ramp
    if morning_ramp_start <= now < full_morning_dt:
        return "morning_ramp"

    # 2. Party mode
    pm = state.get("party_mode", {})
    if pm.get("active") and pm.get("ends_at"):
        ends_at = datetime.fromisoformat(pm["ends_at"])
        if now < ends_at:
            return "party_mode"

    # 3. Standard timeline
    if now < morning_ramp_start:
        return "pre_morning"
    if now < adjusted_sunset:
        return "day"
    if now < force_evening_dt:
        return "evening_ramp"
    if now < night_full_dt:
        return "night_ramp"
    if now < hard_cutoff_dt:
        return "hard_cutoff_ramp"

    # 4. Post hard-cutoff — check for active late-night override
    late_night = state.get("late_night_override")
    if late_night and late_night.get("until"):
        if datetime.fromisoformat(late_night["until"]) > now:
            return "late_night_override"

    return "off"
