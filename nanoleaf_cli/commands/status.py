"""status command — phase, weather, lamp state, party, DND, errors."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from controller.config import CONFIG_PATH, load_config
from controller.dateTime import parse_iso
from controller.phase import calculate_phase
from controller.state import STATE_PATH, load_state, should_respect_dnd
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

    # Lamp backoff
    lfs = state.get("lamp_failure_state", {})
    if lfs.get("consecutive_failures", 0) > 0:
        n = lfs["consecutive_failures"]
        retry = lfs.get("next_retry_at")
        retry_str = parse_iso(retry).strftime("%H:%M") if retry else "?"
        print(f"  lamp        {n} failure(s), retry after {retry_str}")

    # Last error
    err = state.get("last_error")
    if err:
        ts = err.get("timestamp", "?")
        etype = err.get("type", "?")
        msg = err.get("message", "?")
        print(f"  last error  [{ts}] {etype}: {msg}")

    if verbose:
        print(f"  state file  {STATE_PATH}")
        print(f"  config file {CONFIG_PATH}")
