"""preview commands — apply a color for 10 s then revert."""

import time as _time

from controller.config import load_config, load_profiles
from controller.state import get_preview_lock, load_state
from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf.sparkle import build_sparkle_effect, calculate_guard_setting, even_spaced
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


def run_sparkle(args, now=None):
    """Preview the sparkle scatter effect on the lamp for --duration seconds, then revert.

    Uses the current effective profile from state if no HSB overrides are given.
    Reads sparkle_transtime and sparkle_floor_pct from config unless overridden by args.
    """
    config = load_config()

    # Resolve brightness source: args override → state last_applied → config threshold default
    state = load_state()
    last_profile = (state.get("last_applied") or {}).get("profile") or {}

    hue        = getattr(args, "hue",        None)
    sat        = getattr(args, "sat",        None)
    brightness = getattr(args, "brightness", None)

    hue        = int(hue)        if hue        is not None else last_profile.get("hue",        20)
    sat        = int(sat)        if sat        is not None else last_profile.get("saturation",  70)
    brightness = int(brightness) if brightness is not None else last_profile.get("brightness",  config.current_guard_threshold)

    floor = int(args.floor) if getattr(args, "floor", None) is not None else config.sparkle_floor_pct
    duration = int(getattr(args, "duration", 10) or 10)

    # The controller's guard is power-based, not a brightness trigger: it fires
    # only when the colour's draw exceeds the budget. This preview always renders
    # the scatter so it can be inspected regardless of whether it would fire live.

    light = make_light()

    # Fetch panel IDs (use state cache if available)
    panel_ids = state.get("panel_ids") or []
    if not panel_ids:
        try:
            panel_ids = light.get_panel_ids()
        except Exception as exc:
            print_error(f"could not fetch panel IDs: {exc}")
            return
    if not panel_ids:
        print_error("lamp returned no panel IDs — cannot build sparkle effect")
        return

    from controller.config import LightProfile
    profile = LightProfile(mode="hsb", hue=hue, saturation=sat, brightness=brightness)

    sorted_ids = sorted(panel_ids)
    k, floor_bri, ceiling_bri = calculate_guard_setting(
        profile, floor, config.current_guard_threshold, len(sorted_ids)
    )
    if k <= 0:
        # Within budget — no panels would dim in the controller. Dim half at the
        # configured floor for a visible preview so the scatter is observable.
        k = len(sorted_ids) // 2
        floor_bri = int(brightness * min(floor, 100) / 100)
        ceiling_bri = brightness
    dim_ids = even_spaced(sorted_ids, k)
    effect = build_sparkle_effect(
        sorted_ids, dim_ids, profile.hue, profile.saturation,
        floor_bri, ceiling_bri, config.sparkle_transtime,
    )

    print(
        f"  Sparkle preview: hue={hue} sat={sat} bri={brightness} "
        f"transtime={config.sparkle_transtime} floor_bri={floor_bri} ceiling_bri={ceiling_bri} "
        f"panels={len(sorted_ids)} dimmed={len(dim_ids)}"
    )

    def _apply(l):
        if not l.write_effect(effect):
            raise RuntimeError("write_effect failed — lamp rejected payload")

    with get_preview_lock():
        orig = light.get_full_state()
        print(f"  sending sparkle ({len(panel_ids)} panels)...", end="", flush=True)
        try:
            _apply(light)
            # Large animData payloads take ~2s for the lamp to parse and start.
            # Wait before counting down so the timer reflects actual run time.
            _time.sleep(2)
            for i in range(duration, 0, -1):
                print(f"\r  reverting in {i}s...  ", end="", flush=True)
                _time.sleep(1)
            print("\r  reverting...               ", end="", flush=True)
        finally:
            # Power off first to stop the looping animation, then restore.
            # PUT /state alone doesn't reliably exit effect mode mid-loop.
            light.power_off()
            _time.sleep(0.3)
            _revert(light, orig)
            print("\r  done.                       ")
