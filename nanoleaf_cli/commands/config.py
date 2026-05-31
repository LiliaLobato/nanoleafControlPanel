"""config commands — list, get, set, reset, color-name."""

from dataclasses import fields
from datetime import time

from controller.config import Config, load_config, save_config
from nanoleaf.color_helper import rgb_to_hue
from nanoleaf_cli._config_io import load_raw_config
from nanoleaf_cli._formatting import confirm_config_set, fmt_config_value, fmt_time, print_error
from nanoleaf_cli._validation import validate_config_field, validate_rgb_str

_HUE_RANGE_RADIUS = 5


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

    hue = rgb_to_hue(r, g, b)
    lo = max(0, hue - _HUE_RANGE_RADIUS)
    hi = min(359, hue + _HUE_RANGE_RADIUS)
    range_key = f"{lo}-{hi}"

    raw = load_raw_config()
    raw.setdefault("color_names", {})
    old = raw["color_names"].get(range_key)
    raw["color_names"][range_key] = name
    save_config(raw)

    if verbose and old:
        print(f"  ✓ color_names[{range_key!r}] = {name!r}  (was {old!r}, hue {hue}°)")
    else:
        print(f"  ✓ color_names[{range_key!r}] = {name!r}  (hue {hue}°)")
