"""Live lamp end-to-end tests for nanoleafLight.py.

Requires a real Nanoleaf device and credentials in .env.
Run with:  RUN_E2E=1 pytest e2e/test_live_lamp.py -v

These tests make real API calls and physically change the lamp state.
"""

import os
import time

import pytest
from dotenv import load_dotenv

load_dotenv()

RUN_E2E = os.environ.get("RUN_E2E", "").strip() == "1"
pytestmark = pytest.mark.skipif(not RUN_E2E, reason="Set RUN_E2E=1 to run live lamp tests")


@pytest.fixture(scope="module")
def light():
    from nanoleaf.nanoleafLight import NanoleafLight

    name = os.environ["NANOLEAF_NAME"]
    ip = os.environ["NANOLEAF_IP_ADDRESS"]
    token = os.environ["NANOLEAF_AUTH_TOKEN"]
    lamp = NanoleafLight(name=name, ip=ip, auth_token=token)
    assert lamp.check_heartbeat(), "Lamp is not reachable — check IP and token in .env"
    return lamp


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat(light):
    assert light.check_heartbeat() is True


# ---------------------------------------------------------------------------
# Power cycle
# ---------------------------------------------------------------------------


def test_power_cycle(light):
    light.power_on()
    time.sleep(1)
    assert light.get_power() is True

    light.power_off()
    time.sleep(1)
    assert light.get_power() is False

    # restore to on so later tests start from a known state
    light.power_on()
    time.sleep(1)


# ---------------------------------------------------------------------------
# get_full_state — single round-trip read-after-write
# ---------------------------------------------------------------------------


def test_get_full_state_keys(light):
    state = light.get_full_state()
    for key in ("on", "hue", "sat", "brightness", "ct", "colorMode"):
        assert key in state, f"Missing key: {key}"


def test_read_after_write_hsb(light):
    light.power_on()
    time.sleep(0.5)
    light.set_hsb(10, 80, 25)
    time.sleep(1)
    state = light.get_full_state()
    assert abs(state["hue"] - 10) <= 2
    assert abs(state["sat"] - 80) <= 2
    assert abs(state["brightness"] - 25) <= 2


# ---------------------------------------------------------------------------
# Batched set_hsb — all three fields change in one round-trip
# ---------------------------------------------------------------------------


def test_set_hsb_is_batched(light):
    """Verify that set_hsb changes hue, sat, AND brightness in a single call."""
    light.power_on()
    time.sleep(0.5)

    # set a known baseline
    light.set_hsb(0, 0, 50)
    time.sleep(1)

    # set all three fields at once
    light.set_hsb(200, 60, 75)
    time.sleep(1)

    state = light.get_full_state()
    assert abs(state["hue"] - 200) <= 2
    assert abs(state["sat"] - 60) <= 2
    assert abs(state["brightness"] - 75) <= 2


# ---------------------------------------------------------------------------
# Fade test
# ---------------------------------------------------------------------------


def test_fade(light):
    """set_hsb with duration>0 should produce a brightness ramp."""
    light.power_on()
    time.sleep(0.5)
    light.set_hsb(10, 80, 5)
    time.sleep(1)

    # Record baseline before starting the fade so the assertion compares against
    # the actual starting brightness, not a mid-ramp poll that may miss the low end.
    baseline_state = light.get_full_state()
    baseline = baseline_state.get("brightness", 5) if baseline_state else 5
    print(f"Baseline brightness before fade: {baseline}")

    # 10-second fade to brightness 100 (duration=100 = 10s in Nanoleaf units)
    light.set_hsb(10, 80, 100, duration=100)

    readings = []
    for _ in range(12):
        state = light.get_full_state()
        readings.append(state.get("brightness") if state else None)
        time.sleep(1)

    print("Brightness readings during fade:", readings)
    non_none = [r for r in readings if r is not None]
    assert len(non_none) >= 5, "Could not read brightness during fade"
    assert non_none[-1] > baseline, "Brightness did not increase during fade"


# ---------------------------------------------------------------------------
# CRITICAL: colour-while-off (pre-staging validation)
# ---------------------------------------------------------------------------


def test_set_hsb_while_off_side_effect(light):
    """Document hardware behavior: bare set_hsb() while off turns the lamp on.

    The Nanoleaf API does not distinguish between "set colour" and "set colour
    and power on" — any /state PUT without an explicit 'on' field causes the
    device to power on. This test pins that behavior so we know when it changes.

    The controller works around this by always including 'on: false' in the
    batched PUT when pre-staging (see apply_profile in profiles.py).
    """
    light.power_off()
    time.sleep(1)

    light.set_hsb(30, 50, 60)   # no on= argument — raw API behavior
    time.sleep(0.5)

    state = light.get_full_state()
    print(f"State after bare set_hsb() while off: {state}")

    assert state["on"] is True, (
        f"Expected lamp to be ON (hardware side-effect of bare set_hsb()), "
        f"got on={state['on']} — hardware behavior may have changed"
    )


def test_color_while_off_prestaging(light):
    """Verify pre-staging with on=False: colour set while off, lamp stays off,
    correct colour on next power-on.

    This is the behaviour the controller relies on. set_hsb(on=False) includes
    'on: false' in the batched PUT, preventing the side-effect power-on
    documented in test_set_hsb_while_off_side_effect.

    Three things must hold:
      1. set_hsb(on=False) while off does NOT turn the lamp on.
      2. The colour is retained on the device while the lamp is off.
      3. A subsequent power_on() lights the lamp at those values.
    """
    light.power_off()
    time.sleep(1)

    # Pre-stage colour with explicit on=False — this is what the controller sends
    light.set_hsb(30, 50, 60, on=False)
    time.sleep(0.5)

    # 1 + 2: lamp stays off AND colour is already stored on the device
    state_while_off = light.get_full_state()
    print(f"State after set_hsb(on=False) while off: {state_while_off}")

    assert state_while_off["on"] is False, (
        f"Pre-staging FAILED: set_hsb(on=False) still turned the lamp ON. "
        f"Got: {state_while_off}"
    )
    assert abs(state_while_off["hue"] - 30) <= 5, (
        f"Pre-staged hue wrong: expected ~30, got {state_while_off['hue']}"
    )
    assert abs(state_while_off["sat"] - 50) <= 5, (
        f"Pre-staged sat wrong: expected ~50, got {state_while_off['sat']}"
    )
    assert abs(state_while_off["brightness"] - 60) <= 5, (
        f"Pre-staged brightness wrong: expected ~60, got {state_while_off['brightness']}"
    )

    # 3: manual power-on lights at the pre-staged colour without any further colour call
    light.power_on()
    time.sleep(1)

    state_after_on = light.get_full_state()
    print(f"State after power-on from pre-staged colour: {state_after_on}")

    assert abs(state_after_on["hue"] - 30) <= 5, (
        f"Wrong hue after power-on: expected ~30, got {state_after_on['hue']}"
    )
    assert abs(state_after_on["sat"] - 50) <= 5, (
        f"Wrong sat after power-on: expected ~50, got {state_after_on['sat']}"
    )
    assert abs(state_after_on["brightness"] - 60) <= 5, (
        f"Wrong brightness after power-on: expected ~60, got {state_after_on['brightness']}"
    )


# ---------------------------------------------------------------------------
# CT ↔ HSB mode switching
# ---------------------------------------------------------------------------


def test_ct_to_hsb_and_back(light):
    light.power_on()
    time.sleep(0.5)

    light.set_color_temp_and_brightness(6000, 80)
    time.sleep(1)
    state = light.get_full_state()
    assert state["colorMode"] in ("ct", "color_temperature")

    light.set_hsb(30, 60, 70)
    time.sleep(1)
    state = light.get_full_state()
    assert state["colorMode"] in ("hs", "hsb", "effect")

    light.set_color_temp_and_brightness(3000, 50)
    time.sleep(1)
    # should not crash and should return to CT mode
    state = light.get_full_state()
    assert state != {}
