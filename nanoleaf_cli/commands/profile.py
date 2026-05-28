"""profile commands — list, get, set, reset."""

import json

from controller.config import (
    CONFIG_PATH,
    LightProfile,
    PROFILE_DEFAULTS,
    load_profiles,
    save_config,
)
from nanoleaf_cli._formatting import confirm_profile_set, fmt_profile, print_error
from nanoleaf_cli._validation import validate_profile_field, validate_profile_name


def _load_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def run_list(args, now=None):
    from nanoleaf.color_helper import describe_color
    profiles = load_profiles()
    for name, profile in profiles.items():
        desc = describe_color(profile)
        print(f"  {name:<16} {fmt_profile(profile):<28} {desc}")


def run_get(args, now=None):
    from nanoleaf.color_helper import describe_color
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))
    profiles = load_profiles()
    profile = profiles[name]
    desc = describe_color(profile)
    print(f"  {name}: {fmt_profile(profile)} — {desc}")


def run_set(args, now=None):
    verbose = getattr(args, "verbose", False)
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))
    field_name = args.field
    try:
        validated = validate_profile_field(field_name, args.value)
    except Exception as exc:
        print_error(str(exc))

    raw = _load_raw()
    raw.setdefault("profiles", {})
    raw["profiles"].setdefault(name, {})

    prev_profile = None
    if verbose:
        prev_profile = load_profiles().get(name)

    raw["profiles"][name][field_name] = validated
    save_config(raw)

    # Build effective profile from updated raw (avoids cache staleness)
    default = PROFILE_DEFAULTS[name]
    stored = raw["profiles"][name]
    effective = LightProfile(
        mode=stored.get("mode", default.mode),
        hue=stored.get("hue", default.hue),
        saturation=stored.get("saturation", default.saturation),
        brightness=stored.get("brightness", default.brightness),
        color_temp=stored.get("color_temp", default.color_temp),
    )
    confirm_profile_set(name, effective, prev=prev_profile, verbose=verbose)


def run_reset(args, now=None):
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))

    raw = _load_raw()
    profiles_section = raw.get("profiles", {})
    if name not in profiles_section:
        print(f"  {name} is already at its default (not overridden)")
        return
    del raw["profiles"][name]
    if not raw["profiles"]:
        del raw["profiles"]
    save_config(raw)
    default = PROFILE_DEFAULTS[name]
    print(f"  ✓ {name} reset to default: {fmt_profile(default)}")
