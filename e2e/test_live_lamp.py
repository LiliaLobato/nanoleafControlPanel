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
    from nanoleafLight import nanoleafLight

    name = os.environ["NANOLEAF_NAME"]
    ip = os.environ["NANOLEAF_IP_ADDRESS"]
    token = os.environ["NANOLEAF_AUTH_TOKEN"]
    lamp = nanoleafLight(name=name, ip=ip, auth_token=token)
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

    # 10-second fade to brightness 100 (duration=100 = 10s in Nanoleaf units)
    light.set_hsb(10, 80, 100, duration=100)

    readings = []
    for _ in range(12):
        state = light.get_full_state()
        readings.append(state.get("brightness") if state else None)
        time.sleep(1)

    print("Brightness readings during fade:", readings)
    # Value should increase overall (not necessarily monotonic due to polling granularity)
    non_none = [r for r in readings if r is not None]
    assert len(non_none) >= 5, "Could not read brightness during fade"
    assert non_none[-1] > non_none[0], "Brightness did not increase during fade"


# ---------------------------------------------------------------------------
# CRITICAL: colour-while-off (pre-staging validation)
# ---------------------------------------------------------------------------


def test_color_while_off_prestaging(light):
    """Verify that Nanoleaf retains colour state set while powered off.

    This is the key assumption behind the controller's continuous pre-staging
    strategy. If this fails, the manual-on flow needs to be redesigned.
    """
    light.power_off()
    time.sleep(1)

    # Send colour while lamp is OFF
    light.set_hsb(30, 50, 60)
    time.sleep(0.5)

    # Power on — lamp should light at (30, 50, 60) without any further call
    light.power_on()
    time.sleep(1)

    state = light.get_full_state()
    print(f"State after power-on from pre-staged colour: {state}")

    assert abs(state["hue"] - 30) <= 5, (
        f"Pre-staging FAILED: expected hue ~30, got {state['hue']}. "
        "The controller's pre-staging assumption is invalid — redesign the manual-on flow."
    )
    assert abs(state["sat"] - 50) <= 5, f"Pre-staging FAILED: expected sat ~50, got {state['sat']}"
    assert abs(state["brightness"] - 60) <= 5, f"Pre-staging FAILED: expected brightness ~60, got {state['brightness']}"


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
