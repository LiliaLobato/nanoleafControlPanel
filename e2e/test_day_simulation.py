"""Accelerated 24-hour day simulation.

Replays a full day by calling run() at every 2-minute tick with a mocked lamp
and injected weather.  No real device required — run without RUN_E2E:

    pytest e2e/test_day_simulation.py -v

What IS tested here (no other test covers this end-to-end):
  - All 7 phases appear; power=correct for every one of the 720 ticks
  - Phase transitions verified through run() + last_applied, not calculate_phase()
  - Morning ramp brightness is strictly non-decreasing tick-by-tick (5→55)
  - Evening ramp holds exact DAYTIME_ON profile (hue=15, sat=80, bri=33) every tick
  - Night ramp brightness trends down from 33 to ~20 (DAYTIME_ON → NIGHT)
  - Hard cutoff ramp fades brightness ~20→~0 with power=True; lamp turns OFF at 23:00
  - get_weather is called on every cron tick (controller always has current weather)
  - Manual-off → DND overnight → auto-cleared, lamp resumes next morning
  - Manual-on after cutoff → late_night_override → morning_ramp clears it
  - Party mode auto-expires → controller returns to normal phase
  - Adverse weather: adjusted sunset fires ~69 min early; day lights-on at noon

Profile constants (defaults, no config overrides):
  SUNRISE_START  HSB  hue=20  sat=70  bri=5
  SUNRISE_END    HSB  hue=40  sat=20  bri=50
  MORNING        CT   ct=6000         bri=55
  DAYTIME_ON     HSB  hue=15  sat=80  bri=33
  NIGHT          HSB  hue=8   sat=90  bri=20

Phase timeline with sunrise=06:00, sunset=20:00, clear sky:
  pre_morning      [00:00, 06:00)  lamp OFF
  morning_ramp     [06:00, 07:00)  lamp ON  brightness 5→55
  day              [07:00, 20:00)  lamp OFF (no adverse conditions)
  evening_ramp     [20:00, 21:00)  lamp ON  DAYTIME_ON held
  night_ramp       [21:00, 22:00)  lamp ON  brightness 33→20
  hard_cutoff_ramp [22:00, 23:00)  lamp ON  brightness 20→~0
  off              [23:00, 00:00)  lamp OFF
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

# Phases that must have power=False with clear sky and no overrides.
_OFF_PHASES = {"pre_morning", "day", "off"}
# Phases that must have power=True.
_ON_PHASES  = {"morning_ramp", "evening_ramp", "night_ramp", "hard_cutoff_ramp"}


class TestDaySimulation:

    def test_full_24hr_power_matches_phase(self, monkeypatch, mock_lamp, fixed_weather):
        """Every tick's power must match phase semantics; all 7 phases must appear."""
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        phases_seen: set[str] = set()
        errors: list[str] = []

        for tick in _ticks(0, 0, 23, 58):
            ctrl.run(now=tick)
            la = state_mod.load_state()["last_applied"]
            phase, power = la["phase"], la["power"]
            phases_seen.add(phase)

            if phase in _OFF_PHASES and power:
                errors.append(f"{tick.strftime('%H:%M')} phase={phase!r} → power=True (expected False)")
            elif phase in _ON_PHASES and not power:
                errors.append(f"{tick.strftime('%H:%M')} phase={phase!r} → power=False (expected True)")

        assert not errors, "Power/phase mismatches:\n" + "\n".join(errors)

        expected = _OFF_PHASES | _ON_PHASES
        assert phases_seen >= expected, \
            f"Missing phases after full day: {expected - phases_seen}"

    def test_phase_transitions_correct_via_run(self, monkeypatch, mock_lamp, fixed_weather):
        """Phase and power at 7 timestamps verified end-to-end through run() + last_applied.

        Unlike the old test which called calculate_phase() directly, this exercises
        the full controller path: lamp contact, override detection, state write.
        """
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        cases = [
            # (now,          expected_phase,      expected_power)
            (_at(5,  0),  "pre_morning",          False),
            (_at(6, 30),  "morning_ramp",         True),
            (_at(10, 0),  "day",                  False),
            (_at(20, 30), "evening_ramp",         True),
            (_at(21, 30), "night_ramp",           True),
            (_at(22, 30), "hard_cutoff_ramp",     True),
            (_at(23, 30), "off",                  False),
        ]
        for now, exp_phase, exp_power in cases:
            ctrl.run(now=now)
            la = state_mod.load_state()["last_applied"]
            assert la["phase"] == exp_phase, (
                f"At {now.strftime('%H:%M')} expected phase {exp_phase!r}, got {la['phase']!r}"
            )
            assert la["power"] == exp_power, (
                f"At {now.strftime('%H:%M')} expected power={exp_power}, got {la['power']!r}"
            )

    def test_morning_ramp_brightness_strictly_monotonic(self, monkeypatch, mock_lamp, fixed_weather):
        """No brightness dip at any tick during 06:00→06:58; ends ≥50 (MORNING_PROFILE=55).

        Two-stage ramp: stage 1 HSB 5→50, stage 2 cross-mode CT 50→55.
        The cross-mode snap keeps brightness monotonic at the boundary.
        """
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        brightnesses = []
        for tick in _ticks(6, 0, 6, 58):
            ctrl.run(now=tick)
            brightnesses.append(mock_lamp.get_full_state()["brightness"])

        assert len(brightnesses) >= 2

        decreases = [
            (i, brightnesses[i], brightnesses[i + 1])
            for i in range(len(brightnesses) - 1)
            if brightnesses[i + 1] < brightnesses[i]
        ]
        assert not decreases, (
            "Brightness decreased during morning ramp: "
            + ", ".join(f"tick {i}: {a}→{b}" for i, a, b in decreases)
        )
        assert brightnesses[0] <= 10, \
            f"Ramp should start dim (SUNRISE_START bri=5), got {brightnesses[0]}"
        assert brightnesses[-1] >= 50, \
            f"Ramp should end bright (MORNING bri=55), got {brightnesses[-1]}"

    def test_evening_ramp_holds_daytime_on_profile(self, monkeypatch, mock_lamp, fixed_weather):
        """All 30 ticks in evening_ramp: ON with exact DAYTIME_ON values (hue=15, sat=80, bri=33)."""
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        for tick in _ticks(20, 0, 20, 58):
            ctrl.run(now=tick)
            la = state_mod.load_state()["last_applied"]
            p  = la["profile"]
            assert la["power"] is True, \
                f"At {tick.strftime('%H:%M')} lamp should be ON during evening_ramp"
            assert p["hue"] == 15, \
                f"At {tick.strftime('%H:%M')} hue={p['hue']}, expected 15 (DAYTIME_ON)"
            assert p["saturation"] == 80, \
                f"At {tick.strftime('%H:%M')} sat={p['saturation']}, expected 80"
            assert p["brightness"] == 33, \
                f"At {tick.strftime('%H:%M')} bri={p['brightness']}, expected 33"

    def test_night_ramp_brightness_decreases(self, monkeypatch, mock_lamp, fixed_weather):
        """Brightness ramps down from DAYTIME_ON (33) to NIGHT (20) during 21:00→21:58."""
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        # Turn lamp on via evening_ramp so it's already ON when night_ramp starts.
        ctrl.run(now=_at(20, 0))

        brightnesses = []
        for tick in _ticks(21, 0, 21, 58):
            ctrl.run(now=tick)
            brightnesses.append(mock_lamp.get_full_state()["brightness"])

        assert brightnesses[0] == 33, \
            f"Night ramp should start at DAYTIME_ON brightness (33), got {brightnesses[0]}"
        assert brightnesses[-1] == 20, \
            f"Night ramp should end at NIGHT brightness (20), got {brightnesses[-1]}"

        increases = [
            (i, brightnesses[i], brightnesses[i + 1])
            for i in range(len(brightnesses) - 1)
            if brightnesses[i + 1] > brightnesses[i]
        ]
        assert not increases, (
            "Brightness increased during night ramp (should only decrease): "
            + ", ".join(f"tick {i}: {a}→{b}" for i, a, b in increases)
        )

    def test_hard_cutoff_ramp_fades_and_first_off_tick_cuts_power(
        self, monkeypatch, mock_lamp, fixed_weather
    ):
        """Hard cutoff ramp fades brightness ~20→~0 with power=True; 23:00 turns lamp OFF.

        During [22:00, 23:00): target_profile is always non-None (interpolated),
        so power stays True even as brightness approaches 0.
        At 23:00 (off phase): target_profile=None → power becomes False.
        """
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        # Prime: run through night_ramp so lamp is ON at 22:00.
        ctrl.run(now=_at(21, 0))

        brightnesses = []
        for tick in _ticks(22, 0, 22, 58):
            ctrl.run(now=tick)
            la = state_mod.load_state()["last_applied"]
            assert la["power"] is True, \
                f"At {tick.strftime('%H:%M')} power should be True during hard_cutoff_ramp"
            brightnesses.append(mock_lamp.get_full_state()["brightness"])

        assert brightnesses[0] == 20, \
            f"Hard cutoff should start at NIGHT brightness (20), got {brightnesses[0]}"
        assert brightnesses[-1] <= 2, \
            f"Hard cutoff should end near 0 brightness, got {brightnesses[-1]}"

        increases = [
            (i, brightnesses[i], brightnesses[i + 1])
            for i in range(len(brightnesses) - 1)
            if brightnesses[i + 1] > brightnesses[i]
        ]
        assert not increases, (
            "Brightness increased during hard cutoff ramp: "
            + ", ".join(f"tick {i}: {a}→{b}" for i, a, b in increases)
        )

        # First off tick must cut power.
        ctrl.run(now=_at(23, 0))
        la = state_mod.load_state()["last_applied"]
        assert la["phase"] == "off"
        assert la["power"] is False, "First off tick (23:00) must set power=False"

    def test_weather_consulted_on_every_tick(self, monkeypatch, mock_lamp, fixed_weather):
        """get_weather is called on every cron tick — controller always has current weather."""
        import sunrise_sunset_controller as ctrl
        monkeypatch.setattr(ctrl, "NanoleafLight", lambda *_: mock_lamp)
        monkeypatch.setenv("NANOLEAF_IP_ADDRESS", "mock")
        monkeypatch.setenv("NANOLEAF_AUTH_TOKEN", "mock")

        call_count = [0]

        def tracking_weather(*args):
            call_count[0] += 1
            return fixed_weather

        monkeypatch.setattr(ctrl, "get_weather", tracking_weather)

        ticks = _ticks(0, 0, 23, 58)
        for tick in ticks:
            ctrl.run(now=tick)

        assert call_count[0] == len(ticks), (
            f"get_weather should be called on every tick: "
            f"expected {len(ticks)}, got {call_count[0]}"
        )

    def test_manual_on_during_day_turns_lamp_on_and_lockout_keeps_it_on(
        self, monkeypatch, mock_lamp, fixed_weather
    ):
        """Manual-on at 10:00 (day, lamp should be off) must be respected.

        The lamp must turn ON immediately and stay ON for the lockout window
        (day_toggle_lockout_minutes=30) before the controller re-evaluates.

        Regression: before fix, manual_on handler cleared DND but left
        should_be_on=False, so apply_profile immediately staged on=False.
        """
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, fixed_weather)

        # Establish day phase state (lamp off, day phase recorded in last_applied)
        ctrl.run(now=_at(10, 0))
        assert state_mod.load_state()["last_applied"]["power"] is False, \
            "Lamp should be OFF at 10:00 (day, no adverse conditions)"

        # User manually turns lamp on
        mock_lamp._state["on"] = True

        # Controller must respect the manual-on and keep lamp ON
        ctrl.run(now=_at(10, 2))
        state = state_mod.load_state()
        assert state["last_applied"]["power"] is True, \
            "Lamp must be ON immediately after manual-on is detected (bug: was turned off)"
        assert state["last_applied"]["phase"] == "day"

        # Oscillation lockout keeps lamp on for subsequent ticks within 30-min window
        for tick_m in (4, 10, 20, 28):
            ctrl.run(now=_at(10, tick_m))
            assert state_mod.load_state()["last_applied"]["power"] is True, \
                f"Lamp should remain ON at 10:{tick_m:02d} (within 30-min lockout window)"

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
          At 18:50 (before adjusted_sunset ≈ 18:51): still in day phase, lamp OFF.
          At 19:00 (past adjusted_sunset ≈ 18:51, before raw_sunset 20:00):
          phase is evening_ramp and lamp is ON.

        Part 2 — day lights-on:
          At 12:00, sun elevation=15° < dark_elevation_deg=20° with
          clouds=95% > dark_cloud_threshold=75% → is_dark_outside=True →
          day phase turns lamp ON (DAYTIME_ON_PROFILE).
        """
        import controller.state as state_mod
        ctrl = _wire(monkeypatch, mock_lamp, adverse_weather)

        # Part 1a: 18:50 is still before adjusted_sunset → phase is still "day"
        # (lamp is ON because is_dark_outside fires — elevation=15° < 20° with clouds=95%)
        ctrl.run(now=_at(18, 50))
        state = state_mod.load_state()
        assert state["last_applied"]["phase"] == "day", (
            f"At 18:50 (before adjusted sunset ≈18:51) phase should still be 'day', "
            f"got {state['last_applied']['phase']!r}"
        )

        # Part 1b: 19:00 is past adjusted sunset → evening_ramp, lamp ON
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
