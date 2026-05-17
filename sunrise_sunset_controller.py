"""sunrise_sunset_controller.py

Cron-driven controller that runs every 5 minutes and applies the correct
Nanoleaf light state based on sunrise/sunset, weather, and manual overrides.

Usage (crontab):
    */5 * * * * /usr/bin/python3 /home/pi/nanoleafControlPanel/sunrise_sunset_controller.py
"""

import dataclasses
import logging
import os
import time as _time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import filelock
from dotenv import load_dotenv

from color_helper import describe_color
from config import Config, load_config
from dateTime import combine
from log_setup import setup_logging
from nanoleafLight import nanoleafLight, NanoleafConnectionError
from openWeather import OpenWeatherLight
from profiles import (
    apply_profile,
    calculate_effective_color_profile,
    calculate_target_profile,
)
from state import (
    acquire_run_lock,
    apply_dnd_flag,
    clear_dnd_if_expired,
    detect_manual_override,
    handle_lamp_failure,
    handle_lamp_success,
    is_lamp_in_backoff,
    load_state,
    save_state,
    should_respect_dnd,
)
from weather_cache import get_weather

load_dotenv()

LOCAL_TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles"))

logger = logging.getLogger(__name__)


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
        sunrise_dt = weather.get_sunrise_dt(tz=now.tzinfo)
        morning_ramp_start = min(
            sunrise_dt,
            combine(now, config.morning_latest_start),
        )
        adjusted_sunset = weather.get_adjusted_sunset(
            config.cloud_threshold,
            config.adverse_offset_min,
            config.adverse_offset_max,
            tz=now.tzinfo,
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


# ---------------------------------------------------------------------------
# Cron orchestration
# ---------------------------------------------------------------------------

def _run(now: Optional[datetime] = None) -> None:
    """Execute one cron tick of the controller."""
    t0 = _time.monotonic()

    config = load_config()
    setup_logging(config)

    state = load_state()

    if now is None:
        now = datetime.now(tz=LOCAL_TZ)

    logger.debug("─── Run start — %s ───", now.strftime("%H:%M:%S"))

    # --- Weather ---------------------------------------------------------
    weather = get_weather(state, now, config)
    if weather:
        cache = state.get("weather_cache") or {}
        fetched_at = cache.get("fetched_at")
        if fetched_at:
            age_min = (now - datetime.fromisoformat(fetched_at)).total_seconds() / 60
            logger.debug("Weather: cached %.0f min ago", age_min)
        try:
            logger.debug("Sun elevation: %.1f°", weather.get_sun_elevation(at=now))
        except Exception:
            pass
    else:
        logger.debug("Weather: unavailable — running without weather data")

    clear_dnd_if_expired(state, now, config, weather)

    # --- Phase -----------------------------------------------------------
    phase = calculate_phase(now, weather, config, state)
    logger.debug("Phase: %s", phase)

    if phase == "morning_ramp":
        if state.get("late_night_override"):
            state["late_night_override"] = None
            logger.info("Late-night override cleared — morning ramp takes precedence")
        if state.get("party_mode", {}).get("active"):
            state["party_mode"] = {"active": False}
            logger.info("Party mode cleared — morning ramp takes precedence")

    # --- Profiles and power intent ----------------------------------------
    target_profile = calculate_target_profile(phase, now, weather, config, state)
    effective_color = calculate_effective_color_profile(phase, target_profile)
    dnd_active = should_respect_dnd(state, now)
    should_be_on = target_profile is not None and not dnd_active

    logger.debug(
        "Target: %s — on=%s%s",
        describe_color(effective_color),
        should_be_on,
        " (DND active)" if dnd_active else "",
    )

    # --- Lamp backoff guard ----------------------------------------------
    if is_lamp_in_backoff(state, now):
        retry_at = state.get("lamp_failure_state", {}).get("next_retry_at", "?")
        logger.debug("★ BACKOFF ACTIVE: next retry at %s", retry_at)
        logger.info("Lamp in backoff, skipping API call (phase=%s)", phase)
        save_state(state)
        logger.debug("State saved (calculated only, lamp unreachable)")
        return

    # --- Lamp contact ----------------------------------------------------
    light = nanoleafLight(
        os.getenv("NANOLEAF_NAME", "Nanoleaf"),
        os.getenv("NANOLEAF_IP_ADDRESS", ""),
        os.getenv("NANOLEAF_AUTH_TOKEN", ""),
    )
    light_state = light.get_full_state()
    if not light_state:
        handle_lamp_failure(
            state, now, config,
            NanoleafConnectionError("lamp unreachable — get_full_state returned empty"),
        )
        save_state(state)
        return

    logger.debug(
        "Lamp check: %s, HSB(%s, %s, %s)",
        "ON" if light_state.get("on") else "OFF",
        light_state.get("hue", "?"),
        light_state.get("sat", "?"),
        light_state.get("brightness", "?"),
    )

    # --- Override detection and handling ---------------------------------
    last_applied = state.get("last_applied") or {}
    override = detect_manual_override(light_state, last_applied, phase)
    logger.debug(
        "Override: %s (expected %s, actual %s)",
        override,
        "ON" if last_applied.get("power") else "OFF",
        "ON" if light_state.get("on") else "OFF",
    )

    if override == "manual_off":
        if phase == "party_mode":
            # Manual-off during party is the "reset shortcut" — clear party, no DND
            state["party_mode"] = {"active": False}
            should_be_on = False
            logger.info("Manual OFF during party — party mode cleared (no DND)")
        else:
            apply_dnd_flag(state, phase, now, config)
            should_be_on = False
            logger.info(
                "Manual OFF detected (phase=%s) — DND until %s",
                phase, state.get("do_not_disturb_until"),
            )
    elif override == "late_night_trigger":
        state["late_night_override"] = {
            "started_at": now.isoformat(),
            "until": (now + timedelta(minutes=config.late_night_fade_minutes)).isoformat(),
        }
        phase = "late_night_override"
        target_profile = calculate_target_profile(phase, now, weather, config, state)
        effective_color = calculate_effective_color_profile(phase, target_profile)
        should_be_on = True
        logger.info(
            "Late-night override triggered — until %s",
            state["late_night_override"]["until"],
        )
    elif override == "manual_on":
        state["do_not_disturb_until"] = None
        state["dnd_scope"] = None
        logger.info("Manual ON detected (phase=%s) — DND cleared", phase)

    # --- Apply -----------------------------------------------------------
    logger.debug(
        "→ Sending color %s, power %s",
        describe_color(effective_color),
        "ON" if should_be_on else "OFF",
    )

    try:
        ok = apply_profile(light, target_profile, effective_color, should_be_on, light_state)
    except Exception as exc:
        handle_lamp_failure(state, now, config, exc)
        save_state(state)
        return

    if not ok:
        handle_lamp_failure(
            state, now, config,
            NanoleafConnectionError("apply_profile failed"),
        )
        save_state(state)
        return

    # --- State update ----------------------------------------------------
    prev_power = last_applied.get("power")
    if prev_power != should_be_on:
        logger.debug("→ Power: %s → %s", "ON" if prev_power else "OFF", "ON" if should_be_on else "OFF")
    else:
        logger.debug("→ Power: no change (staying %s)", "ON" if should_be_on else "OFF")

    state["last_applied"] = {
        "power": should_be_on,
        "profile": dataclasses.asdict(effective_color),
        "phase": phase,
        "timestamp": now.isoformat(),
    }
    if phase == "day" and prev_power != should_be_on:
        state["last_daytime_toggle_at"] = now.isoformat()

    handle_lamp_success(state)
    save_state(state)
    logger.debug("State saved")

    logger.info(
        "phase=%s override=%s color=%s on=%s",
        phase, override, describe_color(effective_color), should_be_on,
    )
    logger.debug("─── Run complete (%.2fs) ───", _time.monotonic() - t0)


def main(now: Optional[datetime] = None) -> None:
    try:
        lock = acquire_run_lock()
    except filelock.Timeout:
        logger.debug("Another controller instance is running — exiting")
        return

    with lock:
        try:
            _run(now)
        except Exception:
            logger.exception("Unhandled exception in controller — exiting cleanly")


if __name__ == "__main__":
    main()
