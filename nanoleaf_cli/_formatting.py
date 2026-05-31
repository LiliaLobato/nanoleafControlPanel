"""Confirmation messages and formatting helpers for CLI output."""

import sys
from datetime import datetime, time
from typing import Optional

from controller.config import LightProfile
from nanoleaf.color_helper import describe_color


def fmt_time(t: time) -> str:
    """Return 'HH:MM (H:MM AM/PM)' — locale-independent."""
    hour24 = t.strftime("%H:%M")
    hour12 = t.strftime("%-I:%M %p") if sys.platform != "win32" else _fmt_12h(t)
    return f"{hour24} ({hour12})"


def _fmt_12h(t: time) -> str:
    h = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    return f"{h}:{t.minute:02d} {suffix}"


def fmt_profile(profile: LightProfile) -> str:
    """Return a compact profile string: 'HSB(h, s, b)' or 'CT(ct, b)'."""
    if profile.mode == "ct":
        return f"CT({profile.color_temp}, {profile.brightness})"
    return f"HSB({profile.hue}, {profile.saturation}, {profile.brightness})"


def fmt_config_value(key: str, validated_value) -> str:
    """Return a display-ready string for a validated config value.

    Time fields (str "HH:MM") are formatted with both 24h and 12h.
    All other types use repr().
    """
    if isinstance(validated_value, str) and len(validated_value) == 5 and validated_value[2] == ":":
        try:
            t = datetime.strptime(validated_value, "%H:%M").time()
            return fmt_time(t)
        except ValueError:
            pass
    return repr(validated_value)


def confirm_config_set(key: str, display_value: str, *, prev=None, verbose: bool = False) -> None:
    """Print a confirmation line after saving a config value.

    display_value must be a pre-formatted string (caller is responsible for
    formatting — use fmt_config_value() for the standard treatment).
    """
    line = f"  ✓ {key} set to {display_value}"
    if verbose and prev is not None:
        line += f"  (was {prev!r})"
    print(line)


def confirm_profile_set(name: str, profile: LightProfile, *, prev: Optional[LightProfile] = None, verbose: bool = False) -> None:
    """Print a confirmation line after saving a profile."""
    desc = describe_color(profile)
    line = f"  ✓ {name} profile updated: {fmt_profile(profile)} — {desc}"
    if verbose and prev is not None:
        line += f"  (was {fmt_profile(prev)})"
    print(line)


def confirm_party(
    profile: LightProfile,
    ends_at: Optional[datetime],
    fade_minutes: Optional[int],
) -> None:
    """Print a confirmation line when party mode is activated."""
    desc = describe_color(profile)
    parts = [f"  ✓ Party mode ON: {fmt_profile(profile)} — {desc}"]
    if ends_at is not None:
        parts.append(f"ends at {ends_at.strftime('%H:%M')}")
    if fade_minutes is not None:
        parts.append(f"fade {fade_minutes} min before end")
    print(", ".join(parts))


def print_error(msg: str, code: int = 1) -> None:
    """Print msg to stderr and exit with code."""
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)
