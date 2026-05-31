"""status command — phase, weather, lamp state, party, DND, errors."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from controller.config import CONFIG_PATH, load_config
from controller.dateTime import parse_iso
from controller.phase import calculate_phase
from controller.state import STATE_PATH, load_state, should_respect_dnd
from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli._formatting import fmt_time
from weather.weather_cache import reconstruct_cached_weather


def run(args, now=None):
    verbose = getattr(args, "verbose", False)

    if now is None:
        local_tz = ZoneInfo(os.getenv("TIMEZONE", "America/Los_Angeles"))
        now = datetime.now(tz=local_tz)

    config = load_config()
    state = load_state()

    lat = os.getenv("OPENWEATHER_LATITUDE")
    lon = os.getenv("OPENWEATHER_LONGITUDE")
    weather = reconstruct_cached_weather(state, lat, lon)

    phase = calculate_phase(now, weather, config, state)

    print(f"  phase       {phase}")
    print(f"  time        {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # Weather
    cache = state.get("weather_cache")
    if weather:
        tz = now.tzinfo
        sunrise = weather.get_sunrise_dt(tz=tz)
        sunset = weather.get_sunset_dt(tz=tz)
        adj = weather.get_adjusted_sunset(
            config.cloud_threshold,
            config.adverse_offset_min,
            config.adverse_offset_max,
            tz=tz,
        )
        fetched_at = cache.get("fetched_at") if cache else None
        age_str = ""
        if fetched_at:
            age_min = (now - parse_iso(fetched_at)).total_seconds() / 60
            age_str = f"  cache {age_min:.0f} min ago"
        cloud_flag = " (adverse)" if weather.has_adverse_conditions() else ""
        print(
            f"  weather     clouds {weather.weather.clouds}%{cloud_flag}"
            f"  sunrise {sunrise.strftime('%H:%M')}"
            f"  sunset {sunset.strftime('%H:%M')}{age_str}"
        )
        if adj != sunset:
            print(f"              adjusted sunset {adj.strftime('%H:%M')}")
    else:
        print("  weather     no data")

    # Party mode
    pm = state.get("party_mode", {})
    if pm.get("active"):
        ends_at = pm.get("ends_at")
        fade = pm.get("fade_minutes", 0)
        end_str = parse_iso(ends_at).strftime("%H:%M") if ends_at else "?"
        print(f"  party       ON — ends {end_str}, fade {fade} min")

    # Late-night override
    late = state.get("late_night_override")
    if late and late.get("until") and parse_iso(late["until"]) > now:
        print(f"  late-night  override until {parse_iso(late['until']).strftime('%H:%M')}")

    # DND
    if should_respect_dnd(state, now):
        dnd_until = parse_iso(state["do_not_disturb_until"])
        scope = state.get("dnd_scope", "")
        print(f"  DND         until {dnd_until.strftime('%H:%M')} ({scope})")

    # Lamp (live query)
    ip    = os.getenv("NANOLEAF_IP_ADDRESS")
    token = os.getenv("NANOLEAF_AUTH_TOKEN")
    print()
    print("Lamp:")
    if ip and token:
        try:
            lamp_state = NanoleafLight("nanoleaf", ip, token).get_full_state()
        except Exception:
            lamp_state = None
        if lamp_state:
            power_str = "ON" if lamp_state.get("on") else "OFF"
            h = lamp_state.get("hue", "?")
            s = lamp_state.get("sat", "?")
            b = lamp_state.get("brightness", "?")
            print(f"  Power (actual):      {power_str}")
            print(f"  Current HSB:         ({h}, {s}, {b})")
            print(f"  Reachable:           yes")
        else:
            print(f"  Reachable:           no")
    else:
        print(f"  Reachable:           unknown (NANOLEAF_IP_ADDRESS / NANOLEAF_AUTH_TOKEN not set)")

    # Failure state
    lfs = state.get("lamp_failure_state", {})
    n_fails = lfs.get("consecutive_failures", 0)
    last_fail = lfs.get("last_failure_at")
    next_retry = lfs.get("next_retry_at")
    err = state.get("last_error")
    print()
    print("Failure state:")
    print(f"  Consecutive fails:   {n_fails}")
    if last_fail:
        print(f"  Last failure:        {parse_iso(last_fail).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if next_retry:
        print(f"  Next retry:          {parse_iso(next_retry).strftime('%H:%M')}")
    if err:
        etype = err.get("type", "?")
        msg   = err.get("message", "?")
        ts    = err.get("timestamp", "?")
        print(f"  Last error:          [{ts}] {etype}: {msg}")
    else:
        print(f"  Last error:          none")

    if verbose:
        print()
        print(f"  state file  {STATE_PATH}")
        print(f"  config file {CONFIG_PATH}")
