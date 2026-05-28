"""lamp commands — on, off, info."""

import json
import os

from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli._formatting import print_error


def _make_light() -> NanoleafLight:
    ip = os.getenv("NANOLEAF_IP")
    token = os.getenv("NANOLEAF_AUTH_TOKEN")
    if not ip:
        print_error("NANOLEAF_IP is not set")
    if not token:
        print_error("NANOLEAF_AUTH_TOKEN is not set")
    return NanoleafLight("nanoleaf", ip, token)


def run_on(args, now=None):
    light = _make_light()
    ok = light.power_on()
    if ok:
        print("  ✓ lamp on")
    else:
        print_error("could not power on lamp — check connection and auth token")


def run_off(args, now=None):
    light = _make_light()
    ok = light.power_off()
    if ok:
        print("  ✓ lamp off")
    else:
        print_error("could not power off lamp — check connection and auth token")


def run_info(args, now=None):
    light = _make_light()
    try:
        info = light.get_info()
        print(json.dumps(info, indent=2))
    except Exception as exc:
        print_error(f"could not fetch lamp info: {exc}")
