"""profiles.py

Light profile computation for the Nanoleaf controller.
Calculates target and effective color profiles for each phase,
including the two-stage morning ramp. apply_profile() lives here too.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Union

from controller.config import (
    Config,
    LightProfile,
    OFF_PROFILE,
    load_profiles,
)
from controller.dateTime import combine, parse_iso
from nanoleaf.interpolation import interpolate_profiles
from weather.openWeather import OpenWeatherLight
from weather.weather_cache import evaluate_day_darkness

logger = logging.getLogger(__name__)


def _phase_t(now: datetime, start: datetime, end: datetime) -> float:
    """Return interpolation position t in [0.0, 1.0] within a time window."""
    total = (end - start).total_seconds()
    return max(0.0, min(1.0, (now - start).total_seconds() / total)) if total > 0 else 1.0


# ---------------------------------------------------------------------------
# Morning ramp
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
        ramp_start = min(weather.get_sunrise_dt(tz=now.tzinfo), combine(now, config.morning_latest_start))
    else:
        ramp_start = combine(now, config.morning_latest_start)
    ramp_end = combine(now, config.full_morning_time)

    total_secs = (ramp_end - ramp_start).total_seconds()
    elapsed_secs = (now - ramp_start).total_seconds()
    t = max(0.0, min(1.0, elapsed_secs / total_secs)) if total_secs > 0 else 1.0

    profiles = load_profiles()
    stage_1_end = 0.8
    if t <= stage_1_end:
        t1 = t / stage_1_end
        return interpolate_profiles(profiles["SUNRISE_START"], profiles["SUNRISE_END"], t1)
    t2 = (t - stage_1_end) / (1.0 - stage_1_end)
    return interpolate_profiles(profiles["SUNRISE_END"], profiles["MORNING"], t2)


# ---------------------------------------------------------------------------
# Target and effective color profiles
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
    """
    if phase == "pre_morning":
        return None

    if phase == "morning_ramp":
        return _morning_ramp_profile(now, weather, config)

    profiles = load_profiles()

    if phase == "day":
        if evaluate_day_darkness(weather, state, now, config):
            return profiles["DAYTIME_ON"]
        return None

    if phase == "evening_ramp":
        return profiles["DAYTIME_ON"]

    if phase == "night_ramp":
        t = _phase_t(
            now,
            combine(now, config.force_evening_time),
            combine(now, config.night_full_time),
        )
        return interpolate_profiles(profiles["DAYTIME_ON"], profiles["NIGHT"], t)

    if phase == "hard_cutoff_ramp":
        t = _phase_t(
            now,
            combine(now, config.night_full_time),
            combine(now, config.hard_cutoff_time),
        )
        return interpolate_profiles(profiles["NIGHT"], OFF_PROFILE, t)

    if phase == "off":
        return None

    if phase == "late_night_override":
        late_night = state.get("late_night_override") or {}
        started_at = late_night.get("started_at", now.isoformat())
        until = late_night.get("until", now.isoformat())
        t = _phase_t(
            now,
            parse_iso(started_at),
            parse_iso(until),
        )
        return interpolate_profiles(profiles["LATE_NIGHT"], OFF_PROFILE, t)

    if phase == "party_mode":
        pm = state.get("party_mode", {})
        ends_at = parse_iso(pm.get("ends_at") or now.isoformat())
        fade_minutes = pm.get("fade_minutes", config.party_default_fade_minutes)
        if fade_minutes < 0:
            logger.warning("calculate_target_profile: party fade_minutes is negative (%d) — treating as 0", fade_minutes)
            fade_minutes = 0
        pd = pm.get("profile", {})
        default_party = profiles["PARTY"]
        party = LightProfile(
            mode=pd.get("mode", default_party.mode),
            hue=pd.get("hue", default_party.hue),
            saturation=pd.get("saturation", default_party.saturation),
            brightness=pd.get("brightness", default_party.brightness),
            color_temp=pd.get("color_temp", default_party.color_temp),
        )
        if fade_minutes > 0:
            fade_start = ends_at - timedelta(minutes=fade_minutes)
            if now >= fade_start:
                t = _phase_t(now, fade_start, ends_at)
                return interpolate_profiles(party, OFF_PROFILE, t)
        return party

    logger.warning("calculate_target_profile: unknown phase %r", phase)
    return None


# ---------------------------------------------------------------------------
# Profile application
# ---------------------------------------------------------------------------

def apply_profile(
    light: Any,
    effective_color: LightProfile,
    should_be_on: bool,
    light_state: dict,
) -> bool:
    """Send effective_color and power state to the lamp in a single batched call.

    Including 'on' in the color payload prevents the Nanoleaf API from silently
    powering on the lamp as a side effect of a color-while-off pre-staging call.

    Returns True on success, False if the API call failed.
    """
    currently_on = light_state.get("on", False)

    if not should_be_on:
        on_value: Union[bool, None] = False   # keep/set off; blocks side-effect power-on
    elif not currently_on:
        on_value = True                    # turn on with the color in one call
    else:
        on_value = None                    # already on and staying on — omit field

    if effective_color.mode == "ct":
        return light.set_color_temp_and_brightness(
            effective_color.color_temp, effective_color.brightness, on=on_value
        )
    return light.set_hsb(
        effective_color.hue, effective_color.saturation, effective_color.brightness, on=on_value
    )


def calculate_effective_color_profile(
    phase: str,
    target: Optional[LightProfile],
) -> LightProfile:
    """Return the color profile to pre-stage on every cron tick.

    Accepts the pre-computed target (possibly None) and applies the
    fallback rule: always returns a profile so the lamp retains the
    correct color even while powered off, making the next manual-on instant.
    """
    if target is not None:
        return target
    profiles = load_profiles()
    if phase == "day":
        return profiles["DAYTIME_ON"]
    return profiles["NIGHT"]
