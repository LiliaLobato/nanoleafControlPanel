"""preview commands — apply a color for 10 s then revert."""

import time as _time

from controller.config import load_profiles
from controller.state import get_preview_lock
from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli._lamp_factory import make_light
from nanoleaf_cli._formatting import print_error
from nanoleaf_cli._validation import validate_profile_name, validate_rgb_str


def _revert(light: NanoleafLight, orig: dict) -> None:
    if orig:
        light.restore_state(orig)


def _do_preview(light: NanoleafLight, apply_fn) -> None:
    with get_preview_lock():
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
    hue = int(args.value)
    light = make_light()
    _do_preview(light, lambda l: l.set_hsb(hue, 80, 80, on=True))


def run_profile(args, now=None):
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))
        return
    profile = load_profiles()[name]
    light = make_light()

    def _apply_profile(l):
        if profile.mode == "ct":
            l.set_color_temp_and_brightness(profile.color_temp, profile.brightness, on=True)
        else:
            l.set_hsb(profile.hue, profile.saturation, profile.brightness, on=True)

    _do_preview(light, _apply_profile)


def run_hsb(args, now=None):
    hue = int(args.hue)
    sat = int(args.saturation)
    bri = int(args.brightness)
    light = make_light()
    _do_preview(light, lambda l: l.set_hsb(hue, sat, bri, on=True))


def run_color(args, now=None):
    try:
        rgb = validate_rgb_str(args.rgb)
    except Exception as exc:
        print_error(str(exc))
        return
    light = make_light()
    _do_preview(light, lambda l: l.set_color(rgb, on=True))
