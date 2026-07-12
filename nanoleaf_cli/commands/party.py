"""party command — start with options, stop, disable."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta

from controller.config import LightProfile, load_config, load_profiles
from controller.state import load_state, save_state
from nanoleaf.color_helper import rgb_to_hsb
from nanoleaf_cli._formatting import confirm_party, print_error
from nanoleaf_cli._validation import validate_bool, validate_rgb_str, validate_time_str


def run(args, now=None):
    action = getattr(args, "action", None)
    if action in ("stop", "disable"):
        _stop(args, now=now)
    else:
        _start(args, now=now)


def _stop(args, now=None):
    state = load_state()
    pm = state.get("party_mode", {})
    if not pm.get("active"):
        print("  party mode is not active")
        return
    state["party_mode"] = {"active": False}
    save_state(state)
    print("  ✓ party mode stopped")


def _resolve_ends_at(args, config, now: datetime) -> datetime:
    if getattr(args, "until", None):
        try:
            time_str = validate_time_str(args.until)
        except Exception as exc:
            print_error(str(exc))
            return now  # unreachable; return sentinel to satisfy type checker
        h, m = map(int, time_str.split(":"))
        ends_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    else:
        t = config.party_default_end
        ends_at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if ends_at <= now:
        ends_at += timedelta(days=1)
    return ends_at


def _resolve_fade(args, config) -> int:
    if getattr(args, "fade_duration", None) is not None:
        return int(args.fade_duration)
    if getattr(args, "fade", None) is not None:
        try:
            fade_on = validate_bool(args.fade)
        except Exception as exc:
            print_error(str(exc))
            return 0  # unreachable; sentinel to satisfy type checker
        return config.party_default_fade_minutes if fade_on else 0
    return config.party_default_fade_minutes


def _resolve_party_profile(args, default_party: LightProfile) -> LightProfile:
    if getattr(args, "color", None):
        try:
            r, g, b = validate_rgb_str(args.color)
        except Exception as exc:
            print_error(str(exc))
            return default_party  # unreachable; sentinel to satisfy type checker
        hue, sat, bri = rgb_to_hsb(r, g, b)
        return replace(default_party, hue=hue, saturation=sat, brightness=bri, mode="hsb")

    overrides = {}
    if getattr(args, "hue", None) is not None:
        overrides["hue"] = int(args.hue)
    if getattr(args, "sat", None) is not None:
        overrides["saturation"] = int(args.sat)
    if getattr(args, "brightness", None) is not None:
        overrides["brightness"] = int(args.brightness)
    return replace(default_party, **overrides)


def _resolve_sparkle_override(args) -> dict:
    """Build sparkle_override dict from the --floor arg.

    Returns {"floor_pct": N} if --floor was given, else {} (fall back to config
    sparkle_floor_pct at runtime).
    """
    override = {}
    if getattr(args, "floor", None) is not None:
        override["floor_pct"] = int(args.floor)
    return override


def _start(args, now=None):
    config = load_config()

    if now is None:
        now = datetime.now().astimezone()

    ends_at = _resolve_ends_at(args, config, now)
    fade_minutes = _resolve_fade(args, config)
    party_profile = _resolve_party_profile(args, load_profiles()["PARTY"])
    sparkle_override = _resolve_sparkle_override(args)

    state = load_state()
    party_state = {
        "active": True,
        "started_at": now.isoformat(),
        "ends_at": ends_at.isoformat(),
        "fade_minutes": fade_minutes,
        "profile": asdict(party_profile),
    }
    if sparkle_override:
        party_state["sparkle_override"] = sparkle_override
    state["party_mode"] = party_state
    save_state(state)
    confirm_party(party_profile, ends_at, fade_minutes if fade_minutes > 0 else None)
    if sparkle_override:
        print(f"  Sparkle override: floor={sparkle_override['floor_pct']}%")
