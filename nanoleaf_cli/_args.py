"""_args.py — builds the complete argparse parser for nanoleaf-cli."""

import argparse

from nanoleaf_cli.commands.config import (
    run_list as config_list,
    run_get as config_get,
    run_set as config_set,
    run_reset as config_reset,
    run_color_name as config_color_name,
)
from nanoleaf_cli.commands.profile import (
    run_list as profile_list,
    run_get as profile_get,
    run_set as profile_set,
    run_reset as profile_reset,
)
from nanoleaf_cli.commands.preview import (
    run_hue as preview_hue,
    run_profile as preview_profile,
    run_hsb as preview_hsb,
    run_color as preview_color,
    run_sparkle as preview_sparkle,
)
from nanoleaf_cli.commands.party import run as party_run
from nanoleaf_cli.commands.lamp import (
    run_on as lamp_on,
    run_off as lamp_off,
    run_info as lamp_info,
    run_ping as lamp_ping,
)
from nanoleaf_cli._validation import (
    validate_hue as _validate_hue,
    validate_saturation as _validate_saturation,
    validate_brightness as _validate_brightness,
    validate_sparkle_floor as _validate_sparkle_floor,
)
from nanoleaf_cli.commands.status import run as status_run
from nanoleaf_cli.commands.error import run as error_run
from nanoleaf_cli.commands.debug import run_on as debug_on, run_off as debug_off
from nanoleaf_cli.commands.logs import run as logs_run


def _help_func(parser):
    """Return a function that prints parser help and exits 1."""
    def _inner(args, now=None):
        parser.print_help()
        raise SystemExit(1)
    return _inner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nanoleaf-cli",
        description="Control and configure the Nanoleaf sunrise/sunset controller.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="show extra detail (previous values, file paths, full state)",
    )

    top = parser.add_subparsers(dest="command")

    # -------------------------------------------------------------------------
    # status
    # -------------------------------------------------------------------------
    p_status = top.add_parser("status", help="show current phase, weather, lamp state")
    p_status.set_defaults(func=status_run)

    # -------------------------------------------------------------------------
    # error
    # -------------------------------------------------------------------------
    p_error = top.add_parser("error", help="show recent errors from state and log")
    p_error.add_argument(
        "-n", type=int, default=1, metavar="N",
        help="number of log ERROR entries to show (default: 1)",
    )
    p_error.set_defaults(func=error_run)

    # -------------------------------------------------------------------------
    # config
    # -------------------------------------------------------------------------
    p_config = top.add_parser("config", help="read and write controller configuration")
    p_config.set_defaults(func=_help_func(p_config))
    cfg_sub = p_config.add_subparsers(dest="config_action", required=True)

    p_cfg_list = cfg_sub.add_parser("list", help="print all config values")
    p_cfg_list.set_defaults(func=config_list)

    p_cfg_get = cfg_sub.add_parser("get", help="print one config value")
    p_cfg_get.add_argument("key", help="config key name")
    p_cfg_get.set_defaults(func=config_get)

    p_cfg_set = cfg_sub.add_parser("set", help="set a config value")
    p_cfg_set.add_argument("key", help="config key name")
    p_cfg_set.add_argument("value", help="new value")
    p_cfg_set.set_defaults(func=config_set)

    p_cfg_reset = cfg_sub.add_parser("reset", help="remove a config override (restore default)")
    p_cfg_reset.add_argument("key", nargs="?", help="config key to reset (omit with --all)")
    p_cfg_reset.add_argument("--all", action="store_true", help="wipe entire config.json")
    p_cfg_reset.set_defaults(func=config_reset)

    p_cfg_cn = cfg_sub.add_parser(
        "color-name",
        help="name a hue range from a color in HEX, RGB, or CMYK",
    )
    cn_src = p_cfg_cn.add_mutually_exclusive_group(required=True)
    cn_src.add_argument("--hex", metavar="RRGGBB", help="hex color code (no #)")
    cn_src.add_argument("--rgb", metavar="R,G,B", help="RGB values 0-255")
    cn_src.add_argument("--cmyk", metavar="C,M,Y,K", help="CMYK values 0-100")
    p_cfg_cn.add_argument("--name", required=True, metavar="NAME", help="name for this color")
    p_cfg_cn.set_defaults(func=config_color_name)

    # -------------------------------------------------------------------------
    # profile
    # -------------------------------------------------------------------------
    p_profile = top.add_parser("profile", help="read and write light profiles")
    p_profile.set_defaults(func=_help_func(p_profile))
    prof_sub = p_profile.add_subparsers(dest="profile_action", required=True)

    p_prof_list = prof_sub.add_parser("list", help="list all profiles")
    p_prof_list.set_defaults(func=profile_list)

    p_prof_get = prof_sub.add_parser("get", help="print one profile")
    p_prof_get.add_argument("name", help="profile name (e.g. NIGHT)")
    p_prof_get.set_defaults(func=profile_get)

    p_prof_set = prof_sub.add_parser("set", help="set one field of a profile")
    p_prof_set.add_argument("name", help="profile name (e.g. NIGHT)")
    p_prof_set.add_argument("field", help="field name (hue, saturation, brightness, color_temp, mode)")
    p_prof_set.add_argument("value", help="new value")
    p_prof_set.set_defaults(func=profile_set)

    p_prof_reset = prof_sub.add_parser("reset", help="restore a profile to its default")
    p_prof_reset.add_argument("name", help="profile name (e.g. NIGHT)")
    p_prof_reset.set_defaults(func=profile_reset)

    # -------------------------------------------------------------------------
    # preview
    # -------------------------------------------------------------------------
    p_preview = top.add_parser(
        "preview",
        help="apply a color to the lamp for 10s then revert",
    )
    p_preview.set_defaults(func=_help_func(p_preview))
    prev_sub = p_preview.add_subparsers(dest="preview_action", required=True)

    p_prev_hue = prev_sub.add_parser("hue", help="preview a hue value (0-359)")
    p_prev_hue.add_argument("value", type=_validate_hue, help="hue 0-359")
    p_prev_hue.set_defaults(func=preview_hue)

    p_prev_profile = prev_sub.add_parser("profile", help="preview a named light profile")
    p_prev_profile.add_argument("name", help="profile name (e.g. NIGHT)")
    p_prev_profile.set_defaults(func=preview_profile)

    p_prev_hsb = prev_sub.add_parser("hsb", help="preview an HSB triple")
    p_prev_hsb.add_argument("hue", type=_validate_hue, help="hue 0-359")
    p_prev_hsb.add_argument("saturation", type=_validate_saturation, help="saturation 0-100")
    p_prev_hsb.add_argument("brightness", type=_validate_brightness, help="brightness 0-100")
    p_prev_hsb.set_defaults(func=preview_hsb)

    p_prev_color = prev_sub.add_parser("color", help="preview an RGB color")
    p_prev_color.add_argument("rgb", help="R,G,B values 0-255 (e.g. 255,0,128)")
    p_prev_color.set_defaults(func=preview_color)

    p_prev_sparkle = prev_sub.add_parser(
        "sparkle",
        help="preview sparkle scatter effect then revert",
    )
    p_prev_sparkle.add_argument("--hue", type=_validate_hue, metavar="H", help="hue 0-359 (default: from last_applied)")
    p_prev_sparkle.add_argument("--sat", type=_validate_saturation, metavar="S", help="saturation 0-100")
    p_prev_sparkle.add_argument("--brightness", type=_validate_brightness, metavar="B", help="brightness 0-100")
    p_prev_sparkle.add_argument("--floor", type=_validate_sparkle_floor, metavar="N", help="floor brightness %% 0-100 (default: config)")
    p_prev_sparkle.add_argument("--duration", type=int, default=10, metavar="SEC", help="seconds to hold preview (default: 10)")
    p_prev_sparkle.set_defaults(func=preview_sparkle)

    # -------------------------------------------------------------------------
    # party
    # -------------------------------------------------------------------------
    p_party = top.add_parser(
        "party",
        help="start or stop party mode",
    )
    p_party.add_argument(
        "action", nargs="?", choices=["stop", "disable"],
        help="stop/disable ends party mode; omit to start",
    )
    p_party.add_argument("--hue", type=_validate_hue, metavar="H", help="hue 0-359")
    p_party.add_argument("--sat", type=_validate_saturation, metavar="S", help="saturation 0-100")
    p_party.add_argument("--brightness", type=_validate_brightness, metavar="B", help="brightness 0-100")
    p_party.add_argument("--color", metavar="R,G,B", help="RGB color 0-255 per channel")
    p_party.add_argument("--until", metavar="HH:MM", help="end time in 24hr format")
    p_party.add_argument(
        "--fade", metavar="true|false",
        help="fade to off before end time (default: true)",
    )
    p_party.add_argument(
        "--fade-duration", type=int, metavar="MIN", dest="fade_duration",
        help="fade window in minutes (implies --fade true)",
    )
    p_party.add_argument(
        "--floor", type=_validate_sparkle_floor, metavar="N",
        help="sparkle floor brightness %% 0–100; overrides config.sparkle_floor_pct for this session",
    )
    p_party.set_defaults(func=party_run)

    # -------------------------------------------------------------------------
    # lamp
    # -------------------------------------------------------------------------
    p_lamp = top.add_parser("lamp", help="direct lamp control")
    p_lamp.set_defaults(func=_help_func(p_lamp))
    lamp_sub = p_lamp.add_subparsers(dest="lamp_action", required=True)

    p_lamp_on = lamp_sub.add_parser("on", help="turn lamp on")
    p_lamp_on.set_defaults(func=lamp_on)

    p_lamp_off = lamp_sub.add_parser("off", help="turn lamp off")
    p_lamp_off.set_defaults(func=lamp_off)

    p_lamp_info = lamp_sub.add_parser("info", help="dump full device info JSON")
    p_lamp_info.set_defaults(func=lamp_info)

    p_lamp_ping = lamp_sub.add_parser("ping", help="ping lamp and clear backoff if reachable")
    p_lamp_ping.set_defaults(func=lamp_ping)

    # -------------------------------------------------------------------------
    # debug
    # -------------------------------------------------------------------------
    p_debug = top.add_parser("debug", help="toggle verbose controller logging")
    p_debug.set_defaults(func=_help_func(p_debug))
    dbg_sub = p_debug.add_subparsers(dest="debug_action", required=True)

    p_dbg_on = dbg_sub.add_parser("on", help="enable verbose logging")
    p_dbg_on.set_defaults(func=debug_on)

    p_dbg_off = dbg_sub.add_parser("off", help="disable verbose logging")
    p_dbg_off.set_defaults(func=debug_off)

    # -------------------------------------------------------------------------
    # logs
    # -------------------------------------------------------------------------
    p_logs = top.add_parser("logs", help="tail the controller log file")
    p_logs.add_argument(
        "-n", type=int, default=None, metavar="N",
        help="print last N lines and exit (omit to follow in real time)",
    )
    p_logs.set_defaults(func=logs_run)

    return parser
