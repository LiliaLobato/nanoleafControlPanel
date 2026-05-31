"""Shared NanoleafLight factory for CLI commands."""

import os

from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli._formatting import print_error


def make_light() -> NanoleafLight:
    ip = os.getenv("NANOLEAF_IP_ADDRESS")
    token = os.getenv("NANOLEAF_AUTH_TOKEN")
    if not ip:
        print_error("NANOLEAF_IP_ADDRESS is not set")
        return  # unreachable in production; guards test-safety
    if not token:
        print_error("NANOLEAF_AUTH_TOKEN is not set")
        return
    return NanoleafLight("nanoleaf", ip, token)
