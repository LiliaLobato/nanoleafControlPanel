"""party command — start with options, stop, disable."""

import colorsys
from datetime import datetime, timedelta

from controller.config import load_config
from controller.state import load_state, save_state
from nanoleaf_cli._formatting import confirm_party, print_error
from nanoleaf_cli._validation import validate_bool, validate_time_str


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


def _start(args, now=None):
    config = load_config()

    if now is None:
        now = datetime.now().astimezone()

    # ---- Resolve ends_at ----
    if getattr(args, "until", None):
        try:
            time_str = validate_time_str(args.until)
        except Exception as exc:
            print_error(str(exc))
        h, m = map(int, time_str.split(":"))
        ends_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if ends_at <= now:
            ends_at += timedelta(days=1)
    else:
        t = config.party_default_end
        ends_at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if ends_at <= now:
            ends_at += timedelta(days=1)

    # ---- Resolve fade ----
    fade_minutes: int
    if getattr(args, "fade_duration", None) is not None:
        fade_minutes = int(args.fade_duration)
    elif getattr(args, "fade", None) is not None:
        try:
            fade_on = validate_bool(args.fade)
        except Exception as exc:
            print_error(str(exc))
        fade_minutes = config.party_default_fade_minutes if fade_on else 0
    else:
        fade_minutes = config.party_default_fade_minutes

    # ---- Resolve color profile ----
    from controller.config import LightProfile, load_profiles
    profiles = load_profiles()
    default_party = profiles["PARTY"]

    hue = default_party.hue
    sat = default_party.saturation
    bri = default_party.brightness
    mode = default_party.mode
    ct = default_party.color_temp

    if getattr(args, "color", None):
        parts = args.color.split(",")
        if len(parts) != 3:
            print_error("--color must be R,G,B (three comma-separated integers 0-255)")
        try:
            r, g, b = [int(x.strip()) for x in parts]
        except ValueError:
            print_error("--color values must be integers")
        if not all(0 <= c <= 255 for c in (r, g, b)):
            print_error("--color channel values must be 0-255")
        h_norm, s_norm, v_norm = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        hue = round(h_norm * 360)
        sat = round(s_norm * 100)
        bri = round(v_norm * 100)
        mode = "hsb"
    else:
        if getattr(args, "hue", None) is not None:
            hue = int(args.hue)
        if getattr(args, "sat", None) is not None:
            sat = int(args.sat)
        if getattr(args, "brightness", None) is not None:
            bri = int(args.brightness)

    party_profile = LightProfile(mode=mode, hue=hue, saturation=sat, brightness=bri, color_temp=ct)

    # ---- Write state ----
    state = load_state()
    state["party_mode"] = {
        "active": True,
        "ends_at": ends_at.isoformat(),
        "fade_minutes": fade_minutes,
        "profile": {
            "mode": party_profile.mode,
            "hue": party_profile.hue,
            "saturation": party_profile.saturation,
            "brightness": party_profile.brightness,
            "color_temp": party_profile.color_temp,
        },
    }
    save_state(state)
    confirm_party(party_profile, ends_at, fade_minutes if fade_minutes > 0 else None)
