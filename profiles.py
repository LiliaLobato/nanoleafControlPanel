"""profiles.py

Light profile computation for the Nanoleaf controller.
Calculates target and effective color profiles for each phase,
including the two-stage morning ramp. apply_profile() lives here too (task 15).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

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
)
from dateTime import combine
from interpolation import interpolate_profiles
from openWeather import OpenWeatherLight

logger = logging.getLogger(__name__)


def _phase_t(now: datetime, start: datetime, end: datetime) -> float:
    """Return interpolation position t in [0.0, 1.0] within a time window."""
    total = (end - start).total_seconds()
    return max(0.0, min(1.0, (now - start).total_seconds() / total)) if total > 0 else 1.0


# ---------------------------------------------------------------------------
# Morning ramp (Task 9)
# ---------------------------------------------------------------------------

def _morning_ramp_profile(
    now: datetime,
    weather: Optional[OpenWeatherLight],
    config: Config,
) -> LightProfile:
    """Return the interpolated profile for the two-stage morning ramp.

    Stage 1 (first 80% of ramp): SUNRISE_START → SUNRISE_END  (HSB warm amber)
    Stage 2 (last  20% of ramp): SUNRISE_END   → MORNING       (cross-mode CT snap)
    """
    if weather:
        ramp_start = min(weather.get_sunrise_dt(), combine(now, config.morning_latest_start))
    else:
        ramp_start = combine(now, config.morning_latest_start)
    ramp_end = combine(now, config.full_morning_time)

    total_secs = (ramp_end - ramp_start).total_seconds()
    elapsed_secs = (now - ramp_start).total_seconds()
    t = max(0.0, min(1.0, elapsed_secs / total_secs)) if total_secs > 0 else 1.0

    stage_1_end = 0.8
    if t <= stage_1_end:
        t1 = t / stage_1_end
        return interpolate_profiles(SUNRISE_START_PROFILE, SUNRISE_END_PROFILE, t1)
    t2 = (t - stage_1_end) / (1.0 - stage_1_end)
    return interpolate_profiles(SUNRISE_END_PROFILE, MORNING_PROFILE, t2)


# ---------------------------------------------------------------------------
# Target and effective color profiles (Task 7)
# ---------------------------------------------------------------------------

def calculate_target_profile(
    phase: str,
    now: datetime,
    weather: Optional[OpenWeatherLight],
    config: Config,
    state: dict,
) -> Optional[LightProfile]:
    """Return the target light profile for the current phase, or None if power off.

    None means the phase wants the light powered off.
    The oscillation lockout for the day phase is applied in task 11
    (evaluate_day_darkness); for now darkness is evaluated directly.
    """
    if phase == "pre_morning":
        return None

    if phase == "morning_ramp":
        return _morning_ramp_profile(now, weather, config)

    if phase == "day":
        if weather and weather.is_dark_outside(
            config.dark_sun_elevation_deg, config.dark_cloud_threshold
        ):
            return DAYTIME_ON_PROFILE
        return None

    if phase == "evening_ramp":
        return DAYTIME_ON_PROFILE

    if phase == "night_ramp":
        t = _phase_t(
            now,
            combine(now, config.force_evening_time),
            combine(now, config.night_full_time),
        )
        return interpolate_profiles(DAYTIME_ON_PROFILE, NIGHT_PROFILE, t)

    if phase == "hard_cutoff_ramp":
        t = _phase_t(
            now,
            combine(now, config.night_full_time),
            combine(now, config.hard_cutoff_time),
        )
        return interpolate_profiles(NIGHT_PROFILE, OFF_PROFILE, t)

    if phase == "off":
        return None

    if phase == "late_night_override":
        late_night = state.get("late_night_override", {})
        t = _phase_t(
            now,
            datetime.fromisoformat(late_night["started_at"]),
            datetime.fromisoformat(late_night["until"]),
        )
        return interpolate_profiles(LATE_NIGHT_PROFILE, OFF_PROFILE, t)

    if phase == "party_mode":
        pm = state.get("party_mode", {})
        ends_at = datetime.fromisoformat(pm["ends_at"])
        fade_minutes = pm.get("fade_minutes", config.party_default_fade_minutes)
        pd = pm.get("profile", {})
        party = LightProfile(
            mode=pd.get("mode", PARTY_PROFILE.mode),
            hue=pd.get("hue", PARTY_PROFILE.hue),
            saturation=pd.get("saturation", PARTY_PROFILE.saturation),
            brightness=pd.get("brightness", PARTY_PROFILE.brightness),
            color_temp=pd.get("color_temp", PARTY_PROFILE.color_temp),
        )
        if fade_minutes > 0:
            fade_start = ends_at - timedelta(minutes=fade_minutes)
            if now >= fade_start:
                t = _phase_t(now, fade_start, ends_at)
                return interpolate_profiles(party, OFF_PROFILE, t)
        return party

    logger.warning("calculate_target_profile: unknown phase %r", phase)
    return None


def calculate_effective_color_profile(
    phase: str,
    now: datetime,
    weather: Optional[OpenWeatherLight],
    config: Config,
    state: dict,
) -> LightProfile:
    """Return the color profile to pre-stage on every cron tick.

    Always returns a profile (never None) so the lamp retains the correct
    color even while powered off, making the next manual-on instant.
    """
    target = calculate_target_profile(phase, now, weather, config, state)
    if target is not None:
        return target
    if phase == "day":
        return DAYTIME_ON_PROFILE
    return NIGHT_PROFILE
