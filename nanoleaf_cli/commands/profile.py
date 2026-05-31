"""profile commands — list, get, set, reset."""

from controller.config import (
    PROFILE_DEFAULTS,
    LightProfile,
    load_profiles,
    save_config,
)
from nanoleaf.color_helper import describe_color
from nanoleaf_cli._config_io import load_raw_config
from nanoleaf_cli._formatting import confirm_profile_set, fmt_profile, print_error
from nanoleaf_cli._validation import validate_profile_field, validate_profile_name


def run_list(args, now=None):
    profiles = load_profiles()
    for name, profile in profiles.items():
        desc = describe_color(profile)
        print(f"  {name:<16} {fmt_profile(profile):<28} {desc}")


def run_get(args, now=None):
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))
        return
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
        return
    field_name = args.field
    try:
        validated = validate_profile_field(field_name, args.value)
    except Exception as exc:
        print_error(str(exc))
        return

    prev_profile = load_profiles().get(name) if verbose else None

    raw = load_raw_config()
    raw.setdefault("profiles", {})
    raw["profiles"].setdefault(name, {})
    raw["profiles"][name][field_name] = validated
    save_config(raw)

    effective = load_profiles()[name]
    confirm_profile_set(name, effective, prev=prev_profile, verbose=verbose)


def run_reset(args, now=None):
    try:
        name = validate_profile_name(args.name)
    except Exception as exc:
        print_error(str(exc))
        return

    raw = load_raw_config()
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
