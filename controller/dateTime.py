"""dateTime.py

Shared datetime/time utility functions used across the Nanoleaf controller.
"""

from datetime import datetime, time, timezone as _utc
from typing import Any, Optional


def parse_time(value: str) -> time:
    """Parse a time string in HH:MM format."""
    return datetime.strptime(value, "%H:%M").time()


def parse_iso(s: str) -> datetime:
    """Parse an ISO 8601 datetime string, always returning a timezone-aware datetime.

    If the string lacks a UTC offset (e.g., written by an older controller version
    or modified externally), UTC is assumed as a conservative fallback so that
    comparisons against tz-aware `now` never raise TypeError.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_utc.utc)
    return dt


def combine(now: datetime, t: time) -> datetime:
    """Combine today's date with a time value, preserving now's timezone.

    Uses the 3-arg datetime.combine form (Python 3.6+) which attaches the
    timezone without fold disambiguation — times in a DST fold (01:00–02:00 on
    clock-back night) resolve to the first occurrence. Acceptable for cron-tick
    usage where ±1 h imprecision is fine.
    """
    return datetime.combine(now.date(), t, tzinfo=now.tzinfo)


def get_morning_ramp_start(
    now: datetime,
    morning_latest_start: time,
    weather: Optional[Any] = None,
) -> datetime:
    """Return the morning ramp start time, guarded against stale weather caches.

    Uses sunrise from weather only when it falls on today's calendar date.
    A cached response from the previous evening carries yesterday's sunrise
    timestamp, which would make morning_ramp_start fall in the past — causing
    calculate_phase() to enter morning_ramp at midnight and clear_dnd_if_expired()
    to prematurely drop overnight DND.

    Falls back to morning_latest_start whenever weather is absent or stale.
    """
    morning_latest = combine(now, morning_latest_start)
    if weather is None:
        return morning_latest
    sunrise_dt = weather.get_sunrise_dt(tz=now.tzinfo)
    if sunrise_dt.date() != now.date():
        return morning_latest
    return min(sunrise_dt, morning_latest)
