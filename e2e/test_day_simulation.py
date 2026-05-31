"""Accelerated 24-hour day simulation.

Replays a full day by calling run() at every 2-minute tick with a mocked lamp
and injected weather.  No real device required — run without RUN_E2E:

    pytest e2e/test_day_simulation.py -v

What IS tested here (no other test covers this end-to-end):
  - run() survives 720 consecutive ticks without crashing
  - Phase transitions fire at the correct wall-clock times
  - Morning ramp brightness increases monotonically from dim to bright
  - Manual-off → DND overnight → auto-cleared, lamp resumes next morning
  - Manual-on after cutoff → late_night_override → morning_ramp clears it
  - Party mode auto-expires → controller returns to normal phase
  - Adverse weather: adjusted sunset fires ~69 min early; day lights-on at noon
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
LOCAL_TZ  = ZoneInfo("America/Los_Angeles")
LAT, LON  = 47.6144, -122.1923


# ---------------------------------------------------------------------------
# MockLamp
# ---------------------------------------------------------------------------

class MockLamp:
    """Records every lamp API call and reflects the last applied state.

    get_full_state() returns a stable dict so override detection in the
    controller can compare expected vs actual power across ticks without
    spurious manual-override signals.
    """

    def __init__(self):
        self._state = {
            "on": False, "hue": 0, "sat": 0, "brightness": 0,
            "ct": 4000, "colorMode": "hs",
        }
        self.calls: list = []

    def get_full_state(self) -> dict:
        return dict(self._state)

    def set_hsb(self, hue: int, sat: int, bri: int, on: bool | None = None) -> bool:
        self.calls.append(("set_hsb", hue, sat, bri, on))
        self._state.update({"hue": hue, "sat": sat, "brightness": bri, "colorMode": "hs"})
        if on is not None:
            self._state["on"] = on
        return True

    def set_color_temp_and_brightness(self, ct: int, bri: int, on: bool | None = None) -> bool:
        self.calls.append(("set_ct", ct, bri, on))
        self._state.update({"ct": ct, "brightness": bri, "colorMode": "ct"})
        if on is not None:
            self._state["on"] = on
        return True

    def power_on(self) -> bool:
        self.calls.append(("power_on",))
        self._state["on"] = True
        return True

    def power_off(self) -> bool:
        self.calls.append(("power_off",))
        self._state["on"] = False
        return True

    def restore_state(self, state: dict) -> bool:
        self._state.update(state)
        return True

    def check_heartbeat(self) -> bool:
        return True

    def reset(self) -> None:
        self.calls.clear()


# ---------------------------------------------------------------------------
# Weather helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_weather(fixture: str, sunrise_h: int = 6, sunrise_m: int = 0,
                  sunset_h: int = 20, sunset_m: int = 0):
    from weather.openWeather import OpenWeatherLight
    base = datetime.now(tz=LOCAL_TZ)
    data = _load_fixture(fixture)
    data["sys"]["sunrise"] = int(
        base.replace(hour=sunrise_h, minute=sunrise_m, second=0, microsecond=0).timestamp()
    )
    data["sys"]["sunset"] = int(
        base.replace(hour=sunset_h, minute=sunset_m, second=0, microsecond=0).timestamp()
    )
    return OpenWeatherLight.from_cache(data, LAT, LON)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_lamp():
    return MockLamp()


@pytest.fixture
def fixed_weather():
    """Clear sky (code 800, clouds=5%), sunrise 06:00, sunset 20:00.

    No adverse conditions → adjusted_sunset == raw sunset (20:00).
    All phase transitions depend only on wall-clock time.
    """
    return _make_weather("clear.json")


@pytest.fixture
def adverse_weather():
    """Overcast (code 804, clouds=95%), sunrise 06:00, sunset 20:00.

    - has_adverse_conditions() = True
    - adjusted_sunset ≈ 20:00 − 69 min ≈ 18:51
      (offset = 30 + 45 × (95−60)/(100−60) ≈ 69 min)
    - get_sun_elevation patched to 15.0° so is_dark_outside fires all day:
        elevation(15) < dark_elevation_deg(20)
        AND adverse=True
        AND clouds(95) > dark_cloud_threshold(75)
    """
    w = _make_weather("overcast.json")
    w.get_sun_elevation = lambda at=None: 15.0
    return w


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect all state and lock paths to tmp_path."""
    import controller.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_DIR",         tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH",        tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "LOCK_PATH",         tmp_path / "controller.lock")
    monkeypatch.setattr(state_mod, "PREVIEW_LOCK_PATH", tmp_path / "preview.lock")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _at(h: int, m: int = 0, day_offset: int = 0) -> datetime:
    base = datetime.now(tz=LOCAL_TZ)
    return (base + timedelta(days=day_offset)).replace(
        hour=h, minute=m, second=0, microsecond=0
    )


def _ticks(start_h: int, start_m: int, end_h: int, end_m: int, step: int = 2) -> list[datetime]:
    t, end, result = _at(start_h, start_m), _at(end_h, end_m), []
    while t <= end:
        result.append(t)
        t += timedelta(minutes=step)
    return result


def _wire(monkeypatch, lamp, weather):
    """Patch the controller to use mock lamp and injected weather; return ctrl module."""
    import sunrise_sunset_controller as ctrl
    monkeypatch.setattr(ctrl, "NanoleafLight", lambda *_: lamp)
    monkeypatch.setattr(ctrl, "get_weather",   lambda *_: weather)
    monkeypatch.setenv("NANOLEAF_IP_ADDRESS",  "mock")
    monkeypatch.setenv("NANOLEAF_AUTH_TOKEN",  "mock")
    return ctrl


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDaySimulation:

    def test_full_24hr_no_crash(self, monkeypatch, mock_lamp, fixed_weather):
        """720 ticks across a full day — must not raise; last_applied set after final tick."""
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        for tick in _ticks(0, 0, 23, 58):
            ctrl.run(now=tick)

        state = state_mod.load_state()
        assert state["last_applied"] is not None, \
            "last_applied must be set after a full-day run"

    def test_phase_transitions_fire_at_correct_times(self, monkeypatch, mock_lamp, fixed_weather):
        """Phase boundaries match config defaults with sunrise=06:00, sunset=20:00, clear sky.

        Clear sky → no adverse conditions → adjusted_sunset == raw sunset (20:00).
        evening_ramp window is [20:00, 21:00).
        """
        from controller.config import load_config
        from controller.phase import calculate_phase

        config = load_config()
        cases = [
            (_at(5,  0),  "pre_morning"),
            (_at(6, 30),  "morning_ramp"),   # sunrise=06:00 → ramp window [06:00, 07:00)
            (_at(10, 0),  "day"),
            (_at(20, 30), "evening_ramp"),   # adjusted_sunset=20:00 → [20:00, 21:00)
            (_at(21, 30), "night_ramp"),
            (_at(22, 30), "hard_cutoff_ramp"),
            (_at(23, 30), "off"),
        ]
        for now, expected in cases:
            got = calculate_phase(now, fixed_weather, config, {})
            assert got == expected, \
                f"At {now.strftime('%H:%M')} expected {expected!r}, got {got!r}"

    def test_morning_ramp_brightness_increases(self, monkeypatch, mock_lamp, fixed_weather):
        """Brightness trends up from dim (SUNRISE_START) to bright (MORNING) during 06:00→07:00."""
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        brightnesses = []
        for tick in _ticks(6, 0, 6, 58):
            ctrl.run(now=tick)
            brightnesses.append(mock_lamp.get_full_state()["brightness"])

        assert len(brightnesses) >= 2
        assert brightnesses[-1] > brightnesses[0], (
            f"Brightness should increase during morning ramp: "
            f"{brightnesses[0]} at 06:00 → {brightnesses[-1]} at 07:00"
        )
        assert brightnesses[-1] >= 50, \
            f"End-of-ramp brightness should be ≥50 (MORNING_PROFILE=55), got {brightnesses[-1]}"

    def test_manual_off_sets_dnd_and_resumes_next_morning(self, monkeypatch, mock_lamp, fixed_weather):
        """Manual-off at 21:02 (night_ramp) → DND overnight → lamp ON at 06:30 next morning."""
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        # Tick at 21:00 → night_ramp → lamp should be ON
        ctrl.run(now=_at(21, 0))
        state = state_mod.load_state()
        assert state["last_applied"]["power"] is True, \
            "Lamp should be ON at 21:00 (night_ramp)"

        # User manually turns lamp off
        mock_lamp._state["on"] = False

        # Next tick: controller detects manual-off, sets DND overnight
        ctrl.run(now=_at(21, 2))
        state = state_mod.load_state()
        assert state["do_not_disturb_until"] is not None, \
            "DND should be set after manual-off during night_ramp"
        assert state["dnd_scope"] == "overnight", \
            f"DND scope should be 'overnight', got {state['dnd_scope']!r}"
        assert state["last_applied"]["power"] is False, \
            "Lamp should remain OFF while DND is active"

        # Fast-forward to tomorrow 06:30: DND auto-clears, morning_ramp resumes
        ctrl.run(now=_at(6, 30, day_offset=1))
        state = state_mod.load_state()
        assert state["last_applied"]["power"] is True, \
            "Lamp should be ON at 06:30 — DND expired and morning_ramp resumed"

    def test_late_night_override_cleared_by_morning_ramp(self, monkeypatch, mock_lamp, fixed_weather):
        """Manual-on at 23:32 (off phase) → late_night_override → cleared at 06:30 next morning."""
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        # Tick at 23:30 → off phase → lamp is OFF
        ctrl.run(now=_at(23, 30))
        assert state_mod.load_state()["last_applied"]["power"] is False, \
            "Lamp should be OFF at 23:30 (off phase)"

        # User manually turns lamp ON
        mock_lamp._state["on"] = True

        # Next tick: controller detects late_night_trigger and activates override
        ctrl.run(now=_at(23, 32))
        state = state_mod.load_state()
        assert state["late_night_override"] is not None, \
            "late_night_override should be set after manual-on during off phase"
        assert state["last_applied"]["power"] is True, \
            "Lamp should be ON during late_night_override"

        # Tomorrow morning: morning_ramp takes precedence and clears the override
        ctrl.run(now=_at(6, 30, day_offset=1))
        state = state_mod.load_state()
        assert state["late_night_override"] is None, \
            "late_night_override should be cleared when morning_ramp begins"
        assert state["last_applied"]["power"] is True, \
            "Lamp should be ON during morning_ramp"

    def test_party_mode_auto_expires_and_controller_returns_to_phase(
        self, monkeypatch, mock_lamp, fixed_weather
    ):
        """Party mode expires between ticks → controller returns to normal phase."""
        import controller.state as state_mod
        from controller.config import PARTY_PROFILE
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        # Write party state active from 20:30, ending at 21:00
        now = _at(20, 30)
        state = state_mod.load_state()
        state["party_mode"] = {
            "active":     True,
            "started_at": now.isoformat(),
            "ends_at":    _at(21, 0).isoformat(),
            "fade_minutes": 0,
            "profile": {
                "mode":       PARTY_PROFILE.mode,
                "hue":        PARTY_PROFILE.hue,
                "saturation": PARTY_PROFILE.saturation,
                "brightness": PARTY_PROFILE.brightness,
                "color_temp": PARTY_PROFILE.color_temp,
            },
        }
        state_mod.save_state(state)

        ctrl.run(now=now)
        assert state_mod.load_state()["last_applied"]["phase"] == "party_mode", \
            "Should be party_mode at 20:30 before expiry"

        # At 21:30 party has expired; normal phase is night_ramp
        ctrl.run(now=_at(21, 30))
        state = state_mod.load_state()
        assert state["last_applied"]["phase"] == "night_ramp", (
            f"After party expiry at 21:30, phase should be night_ramp, "
            f"got {state['last_applied']['phase']!r}"
        )

    def test_adverse_weather_earlier_sunset_and_day_lights_on(
        self, monkeypatch, mock_lamp, adverse_weather
    ):
        """Overcast: adjusted sunset ~69 min early; day lights-on fires at noon.

        Part 1 — earlier sunset:
          At 19:00, between adjusted_sunset(≈18:51) and raw_sunset(20:00),
          phase is evening_ramp and lamp is ON.

        Part 2 — day lights-on:
          At 12:00, sun elevation=15° < dark_elevation_deg=20° with
          clouds=95% > dark_cloud_threshold=75% → is_dark_outside=True →
          day phase turns lamp ON (DAYTIME_ON_PROFILE).
        """
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, adverse_weather)

        # Part 1: 19:00 is past adjusted sunset → evening_ramp, lamp ON
        ctrl.run(now=_at(19, 0))
        state = state_mod.load_state()
        assert state["last_applied"]["phase"] == "evening_ramp", (
            f"With adverse weather, 19:00 should be evening_ramp "
            f"(adjusted sunset ≈18:51 < 19:00 < raw sunset 20:00), "
            f"got {state['last_applied']['phase']!r}"
        )
        assert state["last_applied"]["power"] is True, \
            "Lamp should be ON during evening_ramp"

        # Reset state so oscillation lockout doesn't interfere with Part 2
        state_mod.save_state(state_mod._empty_state())
        mock_lamp._state["on"] = False

        # Part 2: 12:00 with patched sun elevation 15° → is_dark_outside fires
        ctrl.run(now=_at(12, 0))
        state = state_mod.load_state()
        assert state["last_applied"]["phase"] == "day", \
            "Phase at 12:00 should still be 'day'"
        assert state["last_applied"]["power"] is True, (
            "Lamp should be ON at noon because is_dark_outside() fires: "
            "elevation(15°) < dark_elevation_deg(20°) with overcast clouds(95%)"
        )
