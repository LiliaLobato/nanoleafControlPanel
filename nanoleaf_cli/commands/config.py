"""config commands — list, get, set, reset, color-name."""

import colorsys
import json
from dataclasses import fields
from datetime import time

from controller.config import CONFIG_PATH, Config, load_config, save_config
from nanoleaf_cli._formatting import confirm_config_set, fmt_time, print_error
from nanoleaf_cli._validation import validate_config_field


def _load_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def run_list(args, now=None):
    config = load_config()
    for f in fields(Config):
        val = getattr(config, f.name)
        display = fmt_time(val) if isinstance(val, time) else repr(val)
        print(f"  {f.name:<40} {display}")


def run_get(args, now=None):
    config = load_config()
    key = args.key
    if not hasattr(config, key):
        print_error(f"unknown config key {key!r}")
    val = getattr(config, key)
    print(fmt_time(val) if isinstance(val, time) else repr(val))


def run_set(args, now=None):
    verbose = getattr(args, "verbose", False)
    key = args.key
    try:
        validated = validate_config_field(key, args.value)
    except Exception as exc:
        print_error(str(exc))
    raw = _load_raw()
    prev = raw.get(key)
    raw[key] = validated
    save_config(raw)
    confirm_config_set(key, validated, prev=prev, verbose=verbose)


def run_reset(args, now=None):
    key = getattr(args, "key", None)
    reset_all = getattr(args, "all", False)
    if reset_all:
        save_config({})
        print("  ✓ config reset to defaults")
        return
    if not key:
        print_error("specify a key to reset, or use --all to wipe config")
    raw = _load_raw()
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
        try:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
        except ValueError:
            print_error(f"invalid hex color {args.hex!r}")
    elif args.rgb:
        parts = args.rgb.split(",")
        if len(parts) != 3:
            print_error("--rgb must be R,G,B (three comma-separated integers)")
        try:
            r, g, b = [int(x.strip()) for x in parts]
        except ValueError:
            print_error("--rgb values must be integers")
        if not all(0 <= c <= 255 for c in (r, g, b)):
            print_error("--rgb channel values must be 0-255")
    elif args.cmyk:
        parts = args.cmyk.split(",")
        if len(parts) != 4:
            print_error("--cmyk must be C,M,Y,K (four comma-separated values 0-100)")
        try:
            c, m, y, k = [float(x.strip()) / 100.0 for x in parts]
        except ValueError:
            print_error("--cmyk values must be numbers 0-100")
        r = round(255 * (1 - c) * (1 - k))
        g = round(255 * (1 - m) * (1 - k))
        b = round(255 * (1 - y) * (1 - k))

    h, _s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue = round(h * 360)
    lo = max(0, hue - 5)
    hi = min(360, hue + 5)
    range_key = f"{lo}-{hi}"

    raw = _load_raw()
    raw.setdefault("color_names", {})
    old = raw["color_names"].get(range_key)
    raw["color_names"][range_key] = name
    save_config(raw)

    if verbose and old:
        print(f"  ✓ color_names[{range_key!r}] = {name!r}  (was {old!r}, hue {hue}°)")
    else:
        print(f"  ✓ color_names[{range_key!r}] = {name!r}  (hue {hue}°)")
