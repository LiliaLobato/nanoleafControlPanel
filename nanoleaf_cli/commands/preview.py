"""preview commands — apply a color for 10 s then revert."""

import os
import time as _time

from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli._formatting import print_error
from nanoleaf_cli._validation import validate_profile_name


def _make_light() -> NanoleafLight:
    ip = os.getenv("NANOLEAF_IP_ADDRESS")
    token = os.getenv("NANOLEAF_AUTH_TOKEN")
    if not ip:
        print_error("NANOLEAF_IP_ADDRESS is not set")
    if not token:
        print_error("NANOLEAF_AUTH_TOKEN is not set")
    return NanoleafLight("nanoleaf", ip, token)


def _revert(light: NanoleafLight, orig: dict) -> None:
    if not orig:
        return
    on = orig.get("on", True)
    if orig.get("colorMode") == "ct":
        light.set_color_temp_and_brightness(orig["ct"], orig["brightness"], on=on)
    else:
        light.set_hsb(orig["hue"], orig["sat"], orig["brightness"], on=on)


def _do_preview(light: NanoleafLight, apply_fn) -> None:
    orig = light.get_full_state()
    print("  previewing for 10 seconds...", end="", flush=True)
    try:
        apply_fn(light)
        for i in range(10, 0, -1):
            print(f"\r  reverting in {i}s...  ", end="", flush=True)
            _time.sleep(1)
        print("\r  reverting...               ", end="", flush=True)
    finally:
        _revert(light, orig)
        print("\r  done.                       ")


def run_hue(args, now=None):
    hue = min(int(args.value), 359)
    light = _make_light()
    _do_preview(light, lambda l: l.set_hsb(hue, 80, 80, on=True))


def run_profile(args, now=None):
    from controller.config import load_profiles
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))
    profiles = load_profiles()
    profile = profiles[name]
    light = _make_light()

    def apply(l):
        if profile.mode == "ct":
            l.set_color_temp_and_brightness(profile.color_temp, profile.brightness, on=True)
        else:
            l.set_hsb(
                min(profile.hue, 359), profile.saturation, profile.brightness, on=True
            )

    _do_preview(light, apply)


def run_hsb(args, now=None):
    hue = min(int(args.hue), 359)
    sat = int(args.saturation)
    bri = int(args.brightness)
    light = _make_light()
    _do_preview(light, lambda l: l.set_hsb(hue, sat, bri, on=True))


def run_color(args, now=None):
    parts = args.rgb.split(",")
    if len(parts) != 3:
        print_error("rgb must be R,G,B (three comma-separated integers 0-255)")
    try:
        rgb = tuple(int(x.strip()) for x in parts)
    except ValueError:
        print_error("rgb values must be integers")
    if not all(0 <= c <= 255 for c in rgb):
        print_error("rgb channel values must be 0-255")
    light = _make_light()
    _do_preview(light, lambda l: l.set_color(rgb, on=True))
