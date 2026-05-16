"""dateTime.py

Shared datetime/time utility functions used across the Nanoleaf controller.
"""

from datetime import datetime, time


def parse_time(value: str) -> time:
    """Parse a time string in HH:MM format."""
    return datetime.strptime(value, "%H:%M").time()


def combine(now: datetime, t: time) -> datetime:
    """Combine today's date with a time value, preserving now's timezone."""
    return datetime.combine(now.date(), t).replace(tzinfo=now.tzinfo)
