"""phase.py

Controller phase calculation for the Nanoleaf sunrise/sunset controller.
"""

from datetime import datetime
from typing import Optional

from controller.config import Config
from controller.dateTime import combine, get_morning_ramp_start, parse_iso
from weather.openWeather import OpenWeatherLight


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
    morning_ramp_start = get_morning_ramp_start(now, config.morning_latest_start, weather)
    if weather:
        adjusted_sunset = weather.get_adjusted_sunset(
            config.cloud_threshold,
            config.adverse_offset_min,
            config.adverse_offset_max,
            tz=now.tzinfo,
        )
    else:
        adjusted_sunset = combine(now, config.force_evening_time)

    full_morning_dt  = combine(now, config.full_morning_time)
    force_evening_dt = combine(now, config.force_evening_time)
    night_full_dt    = combine(now, config.night_full_time)
    hard_cutoff_dt   = combine(now, config.hard_cutoff_time)

    # 1. Morning ramp
    # NOTE: if morning_latest_start == full_morning_time (misconfiguration), the
    # window is zero-width and morning_ramp is never returned. load_config() logs
    # a warning when this ordering violation is detected.
    if morning_ramp_start <= now < full_morning_dt:
        return "morning_ramp"

    # 2. Party mode
    pm = state.get("party_mode", {})
    if pm.get("active") and pm.get("ends_at"):
        ends_at = parse_iso(pm["ends_at"])
        if now < ends_at:
            return "party_mode"

    # 3. Standard timeline
    if now < morning_ramp_start:
        return "pre_morning"
    if now < adjusted_sunset:
        return "day"
    # NOTE: when weather is None, adjusted_sunset == force_evening_dt, so the
    # evening_ramp window is zero-width and this branch is never reached.
    # evening_ramp only fires when weather data shifts adjusted_sunset earlier
    # than force_evening_time.
    if now < force_evening_dt:
        return "evening_ramp"
    if now < night_full_dt:
        return "night_ramp"
    if now < hard_cutoff_dt:
        return "hard_cutoff_ramp"

    # 4. Post hard-cutoff — check for active late-night override
    late_night = state.get("late_night_override")
    if late_night and late_night.get("until"):
        if parse_iso(late_night["until"]) > now:
            return "late_night_override"

    return "off"
