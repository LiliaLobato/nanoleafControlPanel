"""lamp commands — on, off, info, ping."""

import json
import os

from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf_cli._formatting import print_error
from controller.state import load_state, save_state, handle_lamp_success


def _make_light() -> NanoleafLight:
    ip = os.getenv("NANOLEAF_IP_ADDRESS")
    token = os.getenv("NANOLEAF_AUTH_TOKEN")
    if not ip:
        print_error("NANOLEAF_IP_ADDRESS is not set")
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


def run_ping(args, now=None):
    light = _make_light()
    reachable = light.check_heartbeat()
    if not reachable:
        print_error("lamp unreachable — backoff not cleared")
        return
    state = load_state()
    failures = state.get("lamp_failure_state", {}).get("consecutive_failures", 0)
    handle_lamp_success(state)
    save_state(state)
    if failures > 0:
        print(f"  ✓ lamp reachable — backoff cleared ({failures} failure(s) reset)")
    else:
        print("  ✓ lamp reachable — no active backoff")
    print("  → applying current controller state...")
    from sunrise_sunset_controller import main as controller_main
    controller_main(now=now)
