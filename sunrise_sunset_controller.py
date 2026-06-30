"""sunrise_sunset_controller.py

Cron-driven controller that applies the correct Nanoleaf light state based
on sunrise/sunset, weather, and manual overrides.

Usage (crontab) — interval must match config.cron_interval_minutes (default 2):
    */2 * * * * /usr/bin/python3 /home/pi/nanoleafControlPanel/sunrise_sunset_controller.py
"""

import dataclasses
import hashlib
import json
import logging
import os
import time as _time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import filelock
from dotenv import load_dotenv

from nanoleaf.color_helper import describe_color
from nanoleaf.sparkle import build_sparkle_effect, calculate_guard_setting, select_dim_panels
from controller.config import load_config
from controller.dateTime import parse_iso
from controller.log_setup import setup_logging
from controller.phase import calculate_phase
from nanoleaf.nanoleafLight import NanoleafLight, NanoleafConnectionError
from controller.profiles import (
    apply_profile,
    calculate_effective_color_profile,
    calculate_target_profile,
)
from controller.state import (
    get_run_lock,
    get_preview_lock,
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
from weather.weather_cache import get_weather

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cron orchestration
# ---------------------------------------------------------------------------

def run(now: Optional[datetime] = None) -> None:
    """Execute one cron tick of the controller."""
    t0 = _time.monotonic()

    # Resolve `now` first so the timestamp reflects actual run start, not post-setup time.
    # LOCAL_TZ is read here (not at module level) so TIMEZONE env var changes take effect
    # between cron ticks and in tests that set the env var after import.
    local_tz = ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles"))
    if now is None:
        now = datetime.now(tz=local_tz)

    config = load_config()
    setup_logging(config)

    state = load_state()
    # Written unconditionally so every save path (incl. backoff / preview-skip
    # early returns) records that the cron is alive — Phase 3 staleness model.
    state["controller_last_tick_at"] = now.isoformat()

    logger.debug("─── Run start — %s ───", now.strftime("%H:%M:%S"))

    # --- Weather ---------------------------------------------------------
    weather = get_weather(state, now, config)
    if weather:
        cache = state.get("weather_cache") or {}
        fetched_at = cache.get("fetched_at")
        if fetched_at:
            try:
                age_min = (now - parse_iso(fetched_at)).total_seconds() / 60
                logger.debug("Weather: cached %.0f min ago", age_min)
            except (ValueError, TypeError) as exc:
                logger.debug("Weather: could not parse cached fetched_at (%s)", exc)
        try:
            logger.debug("Sun elevation: %.1f°", weather.get_sun_elevation(at=now))
        except Exception as exc:
            logger.debug("Sun elevation unavailable: %s", exc)
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
    ip    = os.getenv("NANOLEAF_IP_ADDRESS", "")
    token = os.getenv("NANOLEAF_AUTH_TOKEN", "")
    if not ip:
        logger.warning("NANOLEAF_IP_ADDRESS is not set — lamp contact will fail")
    if not token:
        logger.warning("NANOLEAF_AUTH_TOKEN is not set — lamp contact will fail")
    light = NanoleafLight(
        os.getenv("NANOLEAF_NAME", "Nanoleaf"),
        ip,
        token,
    )
    light_state = light.get_full_state(with_panels=True)
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
    override = detect_manual_override(light_state, last_applied, phase, now=now)
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
            dnd_until = state.get("do_not_disturb_until")
            if dnd_until:
                logger.info("Manual OFF detected (phase=%s) — DND until %s", phase, dnd_until)
            else:
                logger.info("Manual OFF detected (phase=%s) — no DND for this phase", phase)
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
        should_be_on = True
        logger.info("Manual ON detected (phase=%s) — DND cleared, lamp kept ON", phase)
    elif override == "manual_recolor":
        # User recoloured the lamp while our sparkle effect was running, so it
        # left effect mode. Drop the skip-guard hash so the guard re-applies
        # (or set_hsb runs) on this same tick. No DND — the guard simply
        # re-evaluates against the current profile.
        state["sparkle_effect_hash"] = None
        logger.info("Manual recolor detected (phase=%s) — lamp exited sparkle; guard re-evaluates", phase)

    # --- Preview guard ---------------------------------------------------
    try:
        with get_preview_lock():
            pass  # not held; release immediately
    except filelock.Timeout:
        logger.info("Preview session active — skipping lamp changes this tick (phase=%s)", phase)
        save_state(state)
        return

    # --- Current guard ---------------------------------------------------
    # POWER-BASED — no brightness trigger. Every HSB tick we compute the colour's
    # actual per-panel draw and dim K panels (and, if needed, lower the floor at
    # runtime) so total current stays within the PSU budget. Warm/low colours are
    # within budget and skip the guard; bright near-white fires it. The flat
    # brightness cap is only a last-resort fallback (no panel IDs, or CT mode,
    # which cannot be expressed per-panel in animData). write_effect with
    # animType:"static" is firmware-stable for all 51 panels; "custom" is NOT.
    current_guard_active = None
    effect_active = False
    sparkle_effect = None
    sorted_ids: list[int] = []
    dim_ids: list[int] = []

    guard_on = config.current_guard_enabled and should_be_on

    if guard_on and effective_color.mode == "hsb":
        # Panels come from the SAME get_info() as the state read above (via
        # get_full_state(with_panels=True)) — zero extra device GETs per tick, which
        # matters because the guard runs on every high-consumption tick. Reconcile
        # the live set with the cache; on a layout change clear the dim selection so
        # select_dim_panels re-derives it deterministically (even-spacing, not a
        # random reshuffle — no flicker).
        cached_ids = state.get("panel_ids") or []
        live_ids = light_state.get("panel_ids") or []
        panel_ids = live_ids or cached_ids
        if live_ids and set(live_ids) != set(cached_ids):
            if cached_ids:
                logger.info(
                    "current_guard: panel set changed (%d → %d) — resetting dim selection",
                    len(cached_ids), len(live_ids),
                )
                state["sparkle_dim_panels"] = []
                state["sparkle_last_rotation_at"] = None
            state["panel_ids"] = live_ids

        sparkle_override = (state.get("party_mode") or {}).get("sparkle_override", {})
        floor_pct = sparkle_override.get("floor_pct", config.sparkle_floor_pct)
        k, floor_bri, ceiling_bri = calculate_guard_setting(
            effective_color, floor_pct, config.current_guard_threshold, len(panel_ids)
        )

        if panel_ids and k > 0:
            sorted_ids = sorted(panel_ids)
            dim_ids = select_dim_panels(state, sorted_ids, k, now, config)
            sparkle_effect = build_sparkle_effect(
                sorted_ids, dim_ids, effective_color.hue, effective_color.saturation,
                floor_bri, ceiling_bri, config.sparkle_transtime,
            )
            if ceiling_bri < effective_color.brightness:
                # Last resort — even max dimming needed the ceiling lowered too.
                logger.info(
                    "current_guard: sparkle K=%d + ceiling cap %d → %d (last resort)",
                    k, effective_color.brightness, ceiling_bri,
                )
                effective_color = dataclasses.replace(effective_color, brightness=ceiling_bri)
                current_guard_active = "sparkle_capped"
            else:
                logger.debug(
                    "current_guard: sparkle K=%d (floor=%d ceiling=%d)", k, floor_bri, ceiling_bri,
                )
                current_guard_active = "sparkle"
        elif panel_ids:
            # Within budget (K=0) — apply normally, no sparkle, no cap.
            logger.debug("current_guard: HSB within budget (K=0) — no sparkle, no cap")
            state["sparkle_effect_hash"] = None
        else:
            # Over budget but no panel IDs — cannot scatter; flat cap fallback.
            capped = config.current_guard_threshold - 5
            logger.info(
                "current_guard: no panel IDs — capping brightness %d → %d",
                effective_color.brightness, capped,
            )
            effective_color = dataclasses.replace(effective_color, brightness=capped)
            current_guard_active = "brightness_cap"
            state["sparkle_effect_hash"] = None
    elif guard_on and effective_color.mode == "ct":
        # CT cannot be expressed per-panel in animData. Power-based flat cap:
        # treat CT as worst-case white (p_full=1.0) and cap only when the target
        # brightness would exceed the per-panel budget (threshold-5).
        safe_bri = config.current_guard_threshold - 5
        if effective_color.brightness > safe_bri:
            logger.info(
                "current_guard: CT over budget — capping brightness %d → %d",
                effective_color.brightness, safe_bri,
            )
            effective_color = dataclasses.replace(effective_color, brightness=safe_bri)
            current_guard_active = "brightness_cap"
        state["sparkle_effect_hash"] = None
    else:
        state["sparkle_effect_hash"] = None
        if not config.current_guard_enabled:
            logger.debug("current_guard: disabled")
        elif not should_be_on:
            logger.debug("current_guard: skipped — lamp going off")

    # --- Apply -----------------------------------------------------------
    if sparkle_effect is not None:
        effect_hash = hashlib.md5(
            json.dumps(sparkle_effect, sort_keys=True).encode()
        ).hexdigest()
        already_running = (
            state.get("sparkle_effect_hash") == effect_hash
            and light_state.get("colorMode") == "effect"
        )
        # If the lamp was OFF, powering it on drops any volatile effect, so we must
        # rewrite even when the hash matches — never skip a rewrite right after power_on.
        was_off = not light_state.get("on")
        try:
            if was_off:
                light.power_on()
            if already_running and not was_off:
                logger.debug("current_guard: sparkle unchanged — skipping rewrite")
                ok = True
            else:
                ok = light.write_effect(sparkle_effect)
        except Exception as exc:
            handle_lamp_failure(state, now, config, exc)
            save_state(state)
            return
        if not ok:
            # write_effect returned False = the lamp rejected the payload (4xx/5xx).
            # Transient network failures re-raise and are caught above, so this is
            # not a connectivity problem — degrade gracefully to a capped solid
            # colour instead of backing off the lamp for a tick.
            capped = config.current_guard_threshold - 5
            logger.warning(
                "current_guard: write_effect rejected — degrading to capped set_hsb (%d → %d)",
                effective_color.brightness, capped,
            )
            effective_color = dataclasses.replace(effective_color, brightness=capped)
            state["sparkle_effect_hash"] = None
            try:
                ok2 = apply_profile(light, effective_color, should_be_on, light_state)
            except Exception as exc:
                handle_lamp_failure(state, now, config, exc)
                save_state(state)
                return
            if not ok2:
                handle_lamp_failure(
                    state, now, config, NanoleafConnectionError("apply_profile failed"),
                )
                save_state(state)
                return
            current_guard_active = "brightness_cap"
            sparkle_effect = None
        else:
            state["sparkle_effect_hash"] = effect_hash
            current_guard_active = "sparkle"
            effect_active = True
            logger.debug("→ Sparkle written (%d panels, %d dimmed)", len(sorted_ids), len(dim_ids))
    else:
        logger.debug(
            "→ Sending color %s, power %s",
            describe_color(effective_color),
            "ON" if should_be_on else "OFF",
        )
        try:
            ok = apply_profile(light, effective_color, should_be_on, light_state)
        except Exception as exc:
            handle_lamp_failure(state, now, config, exc)
            save_state(state)
            return
        if not ok:
            handle_lamp_failure(
                state, now, config, NanoleafConnectionError("apply_profile failed"),
            )
            save_state(state)
            return

    # --- State update ----------------------------------------------------
    prev_power = last_applied.get("power")
    if prev_power != should_be_on:
        logger.debug("→ Power: %s → %s", "ON" if prev_power else "OFF", "ON" if should_be_on else "OFF")
    else:
        logger.debug("→ Power: no change (staying %s)", "ON" if should_be_on else "OFF")

    last_applied_entry = {
        "power": should_be_on,
        "profile": dataclasses.asdict(effective_color),
        "phase": phase,
        "timestamp": now.isoformat(),
    }
    if current_guard_active:
        last_applied_entry["current_guard_active"] = current_guard_active
    if effect_active:
        # Marks that this tick wrote a sparkle effect, so the next tick can
        # detect a manual recolor (colorMode leaving "effect" while still on).
        last_applied_entry["effect_active"] = True
    state["last_applied"] = last_applied_entry
    if phase == "day" and prev_power != should_be_on:
        state["last_daytime_toggle_at"] = now.isoformat()

    handle_lamp_success(state)
    save_state(state)
    logger.debug("State saved")

    logger.info(
        "phase=%s override=%s color=%s on=%s%s",
        phase, override, describe_color(effective_color), should_be_on,
        f" guard={current_guard_active}" if current_guard_active else "",
    )
    logger.debug("─── Run complete (%.2fs) ───", _time.monotonic() - t0)


# Backwards-compatible alias — tests may import _run directly.
_run = run


def main(now: Optional[datetime] = None) -> None:
    load_dotenv()
    try:
        with get_run_lock():
            try:
                run(now)
            except Exception:
                logger.exception("Unhandled exception in controller — exiting cleanly")
    except filelock.Timeout:
        logger.debug("Another controller instance is running — exiting")


if __name__ == "__main__":
    main()
