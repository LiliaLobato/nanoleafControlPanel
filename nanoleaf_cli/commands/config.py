"""config commands — list, get, set, reset, color-name."""

import sys
from dataclasses import fields
from datetime import time

from controller.config import CONFIG_PATH, Config, LightProfile, load_config, save_config
from nanoleaf.color_helper import describe_color, lookup_hue_range, rgb_to_hsb
from nanoleaf_cli._config_io import load_raw_config
from nanoleaf_cli._formatting import confirm_config_set, fmt_config_value, fmt_time, print_error
from nanoleaf_cli._validation import validate_config_field, validate_rgb_str

_INTERACTIVE_HUE_RADIUS = 10


def _fmt_config_field(val) -> str:
    return fmt_time(val) if isinstance(val, time) else repr(val)


def run_list(args, now=None):
    config = load_config()
    for f in fields(Config):
        print(f"  {f.name:<40} {_fmt_config_field(getattr(config, f.name))}")


def run_get(args, now=None):
    config = load_config()
    key = args.key
    if not hasattr(config, key):
        print_error(f"unknown config key {key!r}")
        return
    print(_fmt_config_field(getattr(config, key)))


def run_set(args, now=None):
    verbose = getattr(args, "verbose", False)
    key = args.key
    try:
        validated = validate_config_field(key, args.value)
    except Exception as exc:
        print_error(str(exc))
        return
    raw = load_raw_config()
    prev = raw.get(key)
    raw[key] = validated
    save_config(raw)
    confirm_config_set(key, fmt_config_value(key, validated), prev=prev, verbose=verbose)


def run_reset(args, now=None):
    key = getattr(args, "key", None)
    reset_all = getattr(args, "all", False)
    if reset_all:
        save_config({})
        print("  ✓ config reset to defaults")
        return
    if not key:
        print_error("specify a key to reset, or use --all to wipe config")
        return
    raw = load_raw_config()
    if key not in raw:
        print(f"  {key} is already at its default (not overridden)")
        return
    del raw[key]
    save_config(raw)
    print(f"  ✓ {key} reset to default")


def _parse_range_str(v: str) -> tuple[int, int]:
    """Parse 'LO-HI' into (lo, hi). Raises ValueError on bad input."""
    parts = v.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"expected LO-HI format, got {v!r}")
    lo, hi = int(parts[0]), int(parts[1])
    if not (0 <= lo <= hi <= 359):
        raise ValueError(f"range must satisfy 0 ≤ lo ≤ hi ≤ 359, got {lo}-{hi}")
    return lo, hi


def _write_color_name(name: str, range_key: str, verbose: bool) -> None:
    raw = load_raw_config()
    raw.setdefault("color_names", {})
    old = raw["color_names"].get(range_key)
    raw["color_names"][range_key] = name
    save_config(raw)
    if verbose and old:
        print(f"  ✓ color_names[{range_key!r}] = {name!r}  (was {old!r})")
    else:
        print(f"  ✓ color_names[{range_key!r}] = {name!r}")


def run_color_name(args, now=None):
    verbose = getattr(args, "verbose", False)
    name = args.name.strip()

    if args.hex:
        hex_str = args.hex.strip().lstrip("#")
        if len(hex_str) != 6:
            print_error("--hex must be exactly 6 hex characters (RRGGBB)")
            return
        try:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
        except ValueError:
            print_error(f"invalid hex color {args.hex!r}")
            return
    elif args.rgb:
        try:
            r, g, b = validate_rgb_str(args.rgb)
        except Exception as exc:
            print_error(str(exc))
            return
    elif args.cmyk:
        parts = args.cmyk.split(",")
        if len(parts) != 4:
            print_error("--cmyk must be C,M,Y,K (four comma-separated values 0-100)")
            return
        try:
            c, m, y, k = [float(x.strip()) / 100.0 for x in parts]
        except ValueError:
            print_error("--cmyk values must be numbers 0-100")
            return
        r = round(255 * (1 - c) * (1 - k))
        g = round(255 * (1 - m) * (1 - k))
        b = round(255 * (1 - y) * (1 - k))

    hue, sat, bri = rgb_to_hsb(r, g, b)
    range_lo, range_hi, current_name = lookup_hue_range(hue)
    default_lo = max(range_lo, hue - _INTERACTIVE_HUE_RADIUS)
    default_hi = min(range_hi, hue + _INTERACTIVE_HUE_RADIUS)
    default_range = f"{default_lo}-{default_hi}"

    input_fmt = (
        f"#{args.hex.strip().lstrip('#').upper()}" if args.hex
        else f"RGB({r},{g},{b})" if args.rgb
        else f"CMYK({args.cmyk})"
    )
    print(f"  Input:     {input_fmt}")
    print(f"  Converted: RGB({r}, {g}, {b}) → HSB({hue}°, {sat}%, {bri}%)")
    print(f"  Hue:       {hue}° falls in range {range_lo}–{range_hi} (currently: \"{current_name}\")")

    if not sys.stdin.isatty():
        # Non-interactive (CI / pipe): write using the default range directly.
        print()
        _write_color_name(name, default_range, verbose)
        return

    # Interactive flow
    print()
    raw_range = input(f"  Enter range for \"{name}\" [default: {default_range}]: ").strip()
    if not raw_range:
        lo, hi = default_lo, default_hi
        range_key = default_range
    else:
        try:
            lo, hi = _parse_range_str(raw_range)
            range_key = f"{lo}-{hi}"
        except ValueError as exc:
            print_error(str(exc))
            return

    mock = LightProfile(mode="hsb", hue=hue, saturation=sat, brightness=bri)
    preview_desc = describe_color(mock)

    print()
    print(f"  Proposed change:")
    print(f"    color_names: {{ \"{range_key}\": \"{name}\" }}")
    print()
    if lo > range_lo:
        print(f"  Remaining range {range_lo}–{lo - 1} stays as \"{current_name}\".")
    if hi < range_hi:
        print(f"  Remaining range {hi + 1}–{range_hi} stays as \"{current_name}\".")
    print(f"  Preview: HSB({hue}, {sat}, {bri}) → \"{preview_desc}\"")
    print()

    ans = input("  Accept? [y/n] ").strip().lower()
    if ans not in ("y", "yes"):
        print("  Cancelled.")
        return

    _write_color_name(name, range_key, verbose)
