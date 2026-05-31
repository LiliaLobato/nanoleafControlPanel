"""weather_cache.py

Weather refresh decision and cache management for the Nanoleaf controller.
Handles anchor-time scheduling, backoff, and reconstruction from cached data.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from weather.openWeather import OpenWeatherLight

from controller.config import Config
from controller.dateTime import parse_iso

logger = logging.getLogger(__name__)


def _is_anchor_time(now: datetime, config: Config) -> bool:
    """Return True if the current cron tick falls within any anchor window.

    The window width equals config.cron_interval_minutes so that exactly one
    tick fires per anchor regardless of the cron frequency.  With */2 cron the
    window is [anchor, anchor+2); with */5 it is [anchor, anchor+5), etc.
    """
    anchors = [
        config.weather_fetch_night,
        config.weather_fetch_morning,
        config.weather_fetch_midday,
        config.weather_fetch_evening,
        config.weather_fetch_late_evening,
    ]
    now_minute = now.hour * 60 + now.minute
    interval = config.cron_interval_minutes
    return any(
        anchor.hour * 60 + anchor.minute <= now_minute
        < min(anchor.hour * 60 + anchor.minute + interval, 1440)
        for anchor in anchors
    )


def _is_weather_cache_fresh(state: dict, now: datetime, config: Config) -> bool:
    cache = state.get("weather_cache")
    if not cache or not cache.get("fetched_at"):
        return False
    fetched_at = parse_iso(cache["fetched_at"])
    return (now - fetched_at).total_seconds() / 3600 < config.weather_cache_max_age_hours


def should_refresh_weather(state: dict, now: datetime, config: Config) -> bool:
    """Return True if a fresh weather API call should be attempted this tick.

    Always True at anchor times (forces retry even during backoff).
    Otherwise True if not in backoff and the cache is missing or stale.
    """
    if _is_anchor_time(now, config):
        return True
    failure = state.get("weather_failure_state", {})
    next_retry = failure.get("next_retry_at")
    if next_retry and parse_iso(next_retry) > now:
        return False  # in backoff, not an anchor — skip
    return not _is_weather_cache_fresh(state, now, config)


def get_weather(state: dict, now: datetime, config: Config) -> Optional[OpenWeatherLight]:
    """Return a weather object for this cron tick.

    Fetches fresh data when should_refresh_weather() is True; otherwise
    reconstructs from cache.  Updates state in place (cache and failure_state).
    Returns None only if no cache is available and the API is unreachable.
    """
    lat   = os.getenv("OPENWEATHER_LATITUDE")
    lon   = os.getenv("OPENWEATHER_LONGITUDE")
    token = os.getenv("OPENWEATHER_AUTH_TOKEN")
    failure = state["weather_failure_state"]

    # Config error: missing env vars — log once and skip both fetch and failure counter.
    if not lat or not lon or not token:
        logger.error(
            "OPENWEATHER_LATITUDE, OPENWEATHER_LONGITUDE, and OPENWEATHER_AUTH_TOKEN must all be set"
        )
        return None

    if should_refresh_weather(state, now, config):
        try:
            weather = OpenWeatherLight(lat, lon, token)
            state["weather_cache"] = {
                "fetched_at": now.isoformat(),
                "raw_data": weather.raw_data,
            }
            if failure["consecutive_failures"] > 0:
                logger.info(
                    "Weather recovered after %d consecutive failures",
                    failure["consecutive_failures"],
                )
            failure["consecutive_failures"] = 0
            failure["last_failure_at"] = None
            failure["next_retry_at"] = None
            return weather
        except Exception as exc:
            failure["consecutive_failures"] += 1
            failure["last_failure_at"] = now.isoformat()
            n = failure["consecutive_failures"]
            schedule = config.backoff_schedule_minutes or [5]
            backoff_min = schedule[min(n - 1, len(schedule) - 1)]
            retry_at = now + timedelta(minutes=backoff_min)
            failure["next_retry_at"] = retry_at.isoformat()
            schedule_len = len(schedule)
            failure_tag = f"{n}/{schedule_len}" if n <= schedule_len else f"{n} (max backoff)"
            logger.warning(
                "Weather API failure %s, backing off until %s (%s)",
                failure_tag, retry_at.strftime("%H:%M"), exc,
            )

    cache = state.get("weather_cache")
    if cache and cache.get("raw_data"):
        return OpenWeatherLight.from_cache(cache["raw_data"], lat, lon)

    logger.error("No weather cache available and API unreachable — running without weather data")
    return None


# ---------------------------------------------------------------------------
# Cache reconstruction helper (used by CLI status command)
# ---------------------------------------------------------------------------

def reconstruct_cached_weather(
    state: dict,
    lat: Optional[str],
    lon: Optional[str],
) -> Optional[OpenWeatherLight]:
    """Reconstruct an OpenWeatherLight from the state cache, or None if unavailable."""
    if not lat or not lon:
        return None
    cache = state.get("weather_cache")
    if not cache or not cache.get("raw_data"):
        return None
    try:
        return OpenWeatherLight.from_cache(cache["raw_data"], lat, lon)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Day-phase darkness evaluation with oscillation lockout
# ---------------------------------------------------------------------------

def evaluate_day_darkness(
    weather: Optional[OpenWeatherLight],
    state: dict,
    now: datetime,
    config: Config,
) -> bool:
    """Return whether it is currently dark outside, with oscillation lockout.

    If the last daytime toggle was within day_toggle_lockout_minutes, returns
    the cached power state from last_applied without re-evaluating weather.
    This prevents the lights from flipping on/off every 5 minutes when sun
    elevation hovers near the threshold on a partly cloudy day.

    State is not modified here — last_daytime_toggle_at is updated by main()
    when a power change is actually applied.
    """
    last_toggle = state.get("last_daytime_toggle_at")
    if last_toggle:
        elapsed_min = (now - parse_iso(last_toggle)).total_seconds() / 60
        # Guard against clock skew (NTP correction backward): a negative elapsed_min
        # would keep the lockout active indefinitely, so we skip it in that case.
        if 0 <= elapsed_min < config.day_toggle_lockout_minutes:
            last_applied = state.get("last_applied") or {}
            return bool(last_applied.get("power", False))

    if not weather:
        logger.debug("evaluate_day_darkness: no weather data — assuming light outside (lamp stays off)")
        return False
    return weather.is_dark_outside(
        config.dark_sun_elevation_deg, config.dark_cloud_threshold, at=now
    )
