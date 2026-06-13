"""lamp commands — on, off, info, ping."""

import json

from controller.config import LightProfile
from controller.state import load_state, save_state, handle_lamp_success
from nanoleaf.color_helper import describe_color
from nanoleaf_cli._lamp_factory import make_light
from nanoleaf_cli._formatting import print_error


def run_on(args, now=None):
    light = make_light()
    ok = light.power_on()
    if ok:
        print("  ✓ lamp on")
    else:
        print_error("could not power on lamp — check connection and auth token")


def run_off(args, now=None):
    light = make_light()
    ok = light.power_off()
    if ok:
        print("  ✓ lamp off")
    else:
        print_error("could not power off lamp — check connection and auth token")


def run_info(args, now=None):
    light = make_light()
    try:
        info = light.get_info()
        print(json.dumps(info, indent=2))
    except Exception as exc:
        print_error(f"could not fetch lamp info: {exc}")


def run_ping(args, now=None):
    light = make_light()
    reachable = light.check_heartbeat()
    if not reachable:
        print_error("lamp unreachable — backoff not cleared")
        return

    state = load_state()
    pre_failures = (state.get("lamp_failure_state") or {}).get("consecutive_failures", 0)
    handle_lamp_success(state)
    save_state(state)

    if pre_failures > 0:
        print(f"  ✓ lamp reachable — backoff cleared ({pre_failures} failure(s) reset)")
    else:
        print("  ✓ lamp reachable")

    print("  → applying current controller state...")
    from sunrise_sunset_controller import main as controller_main
    controller_main(now=now)

    final_state = load_state()
    post_failures = (final_state.get("lamp_failure_state") or {}).get("consecutive_failures", 0)
    if post_failures > pre_failures:
        print_error("controller run failed — lamp may have become unreachable during apply")
        return

    applied = (final_state.get("last_applied") or {})
    if applied:
        profile = applied.get("profile", {})
        color = describe_color(LightProfile(**profile)) if profile else "?"
        power = "on" if applied.get("power") else "off"
        print(f"  ✓ applied: phase={applied.get('phase','?')}  color={color}  lamp={power}")
