"""Live controller end-to-end tests.

Verifies that main() produces the correct lamp state on a real Nanoleaf device
across five key scenarios. These tests exercise the full orchestration chain —
calculate_phase → calculate_target_profile → apply_profile — against real hardware.

Run with: RUN_E2E=1 pytest e2e/test_live_controller.py -v

What is NOT tested here (already covered by unit tests or test_live_lamp.py):
  - Individual nanoleafLight API calls (test_live_lamp.py)
  - Phase boundary logic and interpolation math (tests/test_controller.py)

What IS tested here (no other test touches this):
  - The full pipe from main() down to actual lamp state
  - Pre-staging color reaches the lamp while it is powered off
  - Power on/off transitions driven by the controller
  - Party mode color applied end-to-end
  - Backoff: controller skips lamp contact but still writes state.json
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv

load_dotenv()

RUN_E2E = os.environ.get("RUN_E2E", "").strip() == "1"
pytestmark = pytest.mark.skipif(not RUN_E2E, reason="Set RUN_E2E=1 to run live controller tests")

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
LOCAL_TZ  = ZoneInfo("America/Los_Angeles")
LAT, LON  = 47.6144, -122.1923


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_weather(
    sunrise_hour: int = 5,
    sunrise_min:  int = 30,
    sunset_hour:  int = 20,
    sunset_min:   int = 0,
    fixture:      str = "clear.json",
):
    """Build an OpenWeatherLight with today's sunrise/sunset stamped in."""
    from weather.openWeather import OpenWeatherLight
    today      = datetime.now(tz=LOCAL_TZ)
    sunrise_dt = today.replace(hour=sunrise_hour, minute=sunrise_min, second=0, microsecond=0)
    sunset_dt  = today.replace(hour=sunset_hour,  minute=sunset_min,  second=0, microsecond=0)
    data = _load_fixture(fixture)
    data["sys"]["sunrise"] = int(sunrise_dt.timestamp())
    data["sys"]["sunset"]  = int(sunset_dt.timestamp())
    return OpenWeatherLight.from_cache(data, LAT, LON)


def _now_at(hour: int, minute: int = 0) -> datetime:
    """Return a tz-aware datetime for today at the given hour:minute."""
    today = datetime.now(tz=LOCAL_TZ)
    return today.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def light():
    """Real nanoleafLight instance, verified reachable before any test runs."""
    from nanoleaf.nanoleafLight import NanoleafLight
    lamp = NanoleafLight(
        name=os.environ["NANOLEAF_NAME"],
        ip=os.environ["NANOLEAF_IP_ADDRESS"],
        auth_token=os.environ["NANOLEAF_AUTH_TOKEN"],
    )
    assert lamp.check_heartbeat(), "Lamp unreachable — check IP and token in .env"
    return lamp


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect state.json to a temp directory so tests never touch real state."""
    import controller.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_DIR",  tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "LOCK_PATH",  tmp_path / "controller.lock")


@pytest.fixture(autouse=True)
def restore_lamp_on(light):
    """Leave the lamp ON after every test regardless of what the test did."""
    yield
    light.power_on()
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# 1. Profile applied end-to-end
# ---------------------------------------------------------------------------

def test_evening_ramp_profile_reaches_lamp(monkeypatch, light):
    """main() in evening_ramp applies DAYTIME_ON_PROFILE to the real lamp.

    evening_ramp target is always DAYTIME_ON (HSB 30, 50, 60).  This is the
    most direct end-to-end check of the full pipe:
      calculate_phase → calculate_target_profile → apply_profile → lamp.
    """
    import sunrise_sunset_controller as ctrl
    from controller.config import DAYTIME_ON_PROFILE

    # sunset at 20:00; 20:30 → past sunset, before force_evening (21:00) → evening_ramp
    weather = _make_weather(sunrise_hour=5, sunset_hour=20)
    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: weather)

    light.power_on()
    time.sleep(0.5)

    ctrl.main(now=_now_at(20, 30))
    time.sleep(1)

    state = light.get_full_state()
    print(f"Lamp after evening_ramp run: {state}")

    assert abs(state["hue"]        - DAYTIME_ON_PROFILE.hue)        <= 3, \
        f"Hue mismatch: expected ~{DAYTIME_ON_PROFILE.hue}, got {state['hue']}"
    assert abs(state["sat"]        - DAYTIME_ON_PROFILE.saturation)  <= 3, \
        f"Sat mismatch: expected ~{DAYTIME_ON_PROFILE.saturation}, got {state['sat']}"
    assert abs(state["brightness"] - DAYTIME_ON_PROFILE.brightness)  <= 3, \
        f"Brightness mismatch: expected ~{DAYTIME_ON_PROFILE.brightness}, got {state['brightness']}"


# ---------------------------------------------------------------------------
# 2. Pre-staging while lamp is off
# ---------------------------------------------------------------------------

def test_prestaging_while_off(monkeypatch, light):
    """Controller sends DAYTIME_ON color to the lamp even when power stays off.

    During the day phase with light outside the lamp should be OFF, but the
    controller must still pre-stage DAYTIME_ON_PROFILE on the device.
    Verified in two steps:
      1. After main() the lamp is OFF but HSB matches DAYTIME_ON.
      2. After a manual power_on (no extra API call) the lamp lights at those values.

    This is the integration-layer confirmation of the pre-staging assumption
    validated at the API layer by test_live_lamp.py::test_color_while_off_prestaging.
    """
    import sunrise_sunset_controller as ctrl
    from controller.config import DAYTIME_ON_PROFILE

    weather = _make_weather(sunrise_hour=5, sunset_hour=20, fixture="clear.json")
    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: weather)
    # Force "light outside" so the day phase keeps the lamp off.
    monkeypatch.setattr("controller.profiles.evaluate_day_darkness", lambda *_a, **_kw: False)

    light.power_off()
    time.sleep(1)

    # 10:00 → day phase, clear sky → lamp stays off, but color is pre-staged
    ctrl.main(now=_now_at(10, 0))
    time.sleep(1)

    state = light.get_full_state()
    print(f"Lamp (off) after pre-staging run: {state}")

    assert state["on"] is False, \
        f"Lamp should remain OFF during bright day; got on={state['on']}"
    assert abs(state["hue"]        - DAYTIME_ON_PROFILE.hue)        <= 3, \
        f"Pre-staged hue wrong: expected ~{DAYTIME_ON_PROFILE.hue}, got {state['hue']}"
    assert abs(state["sat"]        - DAYTIME_ON_PROFILE.saturation)  <= 3, \
        f"Pre-staged sat wrong: expected ~{DAYTIME_ON_PROFILE.saturation}, got {state['sat']}"
    assert abs(state["brightness"] - DAYTIME_ON_PROFILE.brightness)  <= 3, \
        f"Pre-staged brightness wrong: expected ~{DAYTIME_ON_PROFILE.brightness}, got {state['brightness']}"

    # Manual power-on: lamp must come up at DAYTIME_ON without any further API call
    light.power_on()
    time.sleep(1)
    state_on = light.get_full_state()
    print(f"Lamp after manual-on from pre-staged color: {state_on}")

    assert abs(state_on["hue"] - DAYTIME_ON_PROFILE.hue) <= 3, (
        f"Post-manual-on hue wrong: expected ~{DAYTIME_ON_PROFILE.hue}, got {state_on['hue']}. "
        "Pre-staging assumption may be invalid for this device."
    )


# ---------------------------------------------------------------------------
# 3a. Phase power-on — morning_ramp turns lamp ON
# ---------------------------------------------------------------------------

def test_morning_ramp_turns_lamp_on(monkeypatch, light):
    """Controller turns the lamp ON when the phase is morning_ramp.

    sunrise 05:30 → ramp 05:30–07:00; inject 06:00 → morning_ramp.
    Lamp starts OFF; after main() it must be ON.
    """
    import sunrise_sunset_controller as ctrl

    weather = _make_weather(sunrise_hour=5, sunrise_min=30, sunset_hour=20)
    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: weather)

    light.power_off()
    time.sleep(1)

    ctrl.main(now=_now_at(6, 0))
    time.sleep(1)

    state = light.get_full_state()
    assert state["on"] is True, \
        f"Lamp should be ON during morning_ramp at 06:00; got on={state['on']}"


# ---------------------------------------------------------------------------
# 3b. Phase power-off — pre_morning turns lamp OFF
# ---------------------------------------------------------------------------

def test_pre_morning_turns_lamp_off(monkeypatch, light):
    """Controller turns the lamp OFF when the phase is pre_morning.

    sunrise 06:30 (late winter); morning_latest_start default 06:00;
    min(06:30, 06:00) = 06:00; at 05:00 we are before 06:00 → pre_morning.
    Lamp starts ON; after main() it must be OFF.
    """
    import sunrise_sunset_controller as ctrl

    weather = _make_weather(sunrise_hour=6, sunrise_min=30, sunset_hour=20)
    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: weather)

    light.power_on()
    time.sleep(1)

    ctrl.main(now=_now_at(5, 0))
    time.sleep(1)

    state = light.get_full_state()
    assert state["on"] is False, \
        f"Lamp should be OFF during pre_morning at 05:00; got on={state['on']}"


# ---------------------------------------------------------------------------
# 4. Party mode color applied end-to-end
# ---------------------------------------------------------------------------

def test_party_mode_color_reaches_lamp(monkeypatch, light):
    """main() with party_mode active applies the party profile to the lamp.

    State is written directly (simulating what nanoleaf-cli party would do)
    then main() is called.  Lamp must end up at PARTY_PROFILE HSB values.
    """
    import controller.state as state_mod
    import sunrise_sunset_controller as ctrl
    from controller.config import PARTY_PROFILE

    weather = _make_weather(sunrise_hour=5, sunset_hour=20)
    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: weather)

    now     = _now_at(21, 0)
    ends_at = now.replace(hour=23, minute=0)

    party_state = state_mod._empty_state()
    party_state["party_mode"] = {
        "active":     True,
        "started_at": now.isoformat(),
        "ends_at":    ends_at.isoformat(),
        "fade_minutes": 30,
        "profile": {
            "mode":       PARTY_PROFILE.mode,
            "hue":        PARTY_PROFILE.hue,
            "saturation": PARTY_PROFILE.saturation,
            "brightness": PARTY_PROFILE.brightness,
            "color_temp": PARTY_PROFILE.color_temp,
        },
    }
    state_mod.save_state(party_state)

    light.power_on()
    time.sleep(0.5)

    ctrl.main(now=now)
    time.sleep(1)

    state = light.get_full_state()
    print(f"Lamp during party_mode: {state}")

    assert abs(state["hue"]        - PARTY_PROFILE.hue)        <= 3, \
        f"Party hue mismatch: expected ~{PARTY_PROFILE.hue}, got {state['hue']}"
    assert abs(state["sat"]        - PARTY_PROFILE.saturation)  <= 3, \
        f"Party sat mismatch: expected ~{PARTY_PROFILE.saturation}, got {state['sat']}"
    assert abs(state["brightness"] - PARTY_PROFILE.brightness)  <= 3, \
        f"Party brightness mismatch: expected ~{PARTY_PROFILE.brightness}, got {state['brightness']}"


# ---------------------------------------------------------------------------
# 5. Backoff: lamp unreachable — skips API, writes state
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_backoff_skips_lamp_but_updates_state(monkeypatch):
    """Controller with unreachable lamp fails gracefully and enters backoff.

    Run 1: lamp call fails → state.lamp_failure_state.consecutive_failures = 1,
           next_retry_at is set.
    Run 2: still within backoff window → controller exits early without touching
           the lamp, consecutive_failures stays at 1.

    Note: 192.0.2.1 is TEST-NET (RFC 5737) — guaranteed unreachable, no route.
    The connection attempt will time out after ~3 s (nanoleafLight connect timeout).
    """
    import controller.state as state_mod
    import sunrise_sunset_controller as ctrl

    weather = _make_weather(sunrise_hour=5, sunset_hour=20)
    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: weather)
    monkeypatch.setenv("NANOLEAF_IP_ADDRESS", "192.0.2.1")

    now = _now_at(10, 0)  # day phase — deterministic, weather-independent

    # Run 1: unreachable → should log warning and set backoff, not crash  (~3 s)
    ctrl.main(now=now)

    saved = state_mod.load_state()
    failures = saved["lamp_failure_state"]
    assert failures["consecutive_failures"] == 1, (
        f"Expected 1 consecutive failure after unreachable lamp, "
        f"got {failures['consecutive_failures']}"
    )
    assert failures["next_retry_at"] is not None, \
        "Expected next_retry_at to be set after first failure"

    # Run 2: inside backoff window → controller must exit before lamp contact,
    # so consecutive_failures must not increment again  (instant — no TCP attempt)
    ctrl.main(now=now)

    saved2 = state_mod.load_state()
    assert saved2["lamp_failure_state"]["consecutive_failures"] == 1, (
        f"consecutive_failures should stay at 1 during backoff, "
        f"got {saved2['lamp_failure_state']['consecutive_failures']}"
    )


# ---------------------------------------------------------------------------
# 6/7. Power-based current guard — fires on over-budget colour, dormant on warm
# ---------------------------------------------------------------------------

def _seed_party_profile(hue, sat, brightness, now):
    """Write an active party_mode with a custom HSB profile (bypasses the timeline
    so the guard sees exactly this colour)."""
    import controller.state as state_mod
    st = state_mod._empty_state()
    st["party_mode"] = {
        "active":       True,
        "started_at":   now.isoformat(),
        "ends_at":      now.replace(hour=23, minute=59).isoformat(),
        "fade_minutes": 0,
        "profile": {"mode": "hsb", "hue": hue, "saturation": sat,
                    "brightness": brightness, "color_temp": 0},
    }
    state_mod.save_state(st)


def test_live_guard_fires_on_white_high_brightness(monkeypatch, light):
    """A near-white, high-brightness colour is over the power budget, so the
    controller writes a static sparkle effect (lamp ends in colorMode 'effect')."""
    import sunrise_sunset_controller as ctrl

    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: _make_weather(sunset_hour=20))
    now = _now_at(21, 0)
    _seed_party_profile(hue=0, sat=0, brightness=95, now=now)

    light.power_on()
    time.sleep(0.5)
    ctrl.main(now=now)
    time.sleep(2)

    state = light.get_full_state()
    print(f"Lamp after near-white high-brightness guard run: {state}")
    assert state != {}, "lamp unreachable after guard run — possible crash"
    assert state["colorMode"] == "effect", (
        f"near-white bri95 is over budget — expected sparkle (colorMode 'effect'), "
        f"got {state['colorMode']!r}"
    )


def test_live_guard_dormant_on_warm_color(monkeypatch, light):
    """A warm, saturated colour draws little power and stays within budget, so the
    power-based guard stays dormant — the lamp shows a solid colour, not an effect."""
    import sunrise_sunset_controller as ctrl

    monkeypatch.setattr(ctrl, "get_weather", lambda *_a, **_kw: _make_weather(sunset_hour=20))
    now = _now_at(21, 0)
    _seed_party_profile(hue=15, sat=80, brightness=70, now=now)

    light.power_on()
    time.sleep(0.5)
    ctrl.main(now=now)
    time.sleep(1)

    state = light.get_full_state()
    print(f"Lamp after warm-colour guard run: {state}")
    assert state["colorMode"] != "effect", (
        f"warm HSB(15,80,70) is within budget — guard should stay dormant (solid), "
        f"got colorMode {state['colorMode']!r}"
    )
    assert abs(state["hue"] - 15) <= 4, f"expected warm hue ~15, got {state['hue']}"
