"""Unit tests for controller logic: phase calculation, profiles, interpolation,
manual overrides, DND, oscillation lockout, late-night override, backoff,
and describe_color.
"""

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from color_helper import describe_color
from config import (
    Config,
    LightProfile,
    DAYTIME_ON_PROFILE,
    LATE_NIGHT_PROFILE,
    MORNING_PROFILE,
    NIGHT_PROFILE,
    OFF_PROFILE,
    SUNRISE_END_PROFILE,
    SUNRISE_START_PROFILE,
)
from interpolation import interpolate_profiles, lerp_hue
from profiles import (
    calculate_effective_color_profile,
    calculate_target_profile,
)
from state import (
    apply_dnd_flag,
    clear_dnd_if_expired,
    detect_manual_override,
    handle_lamp_failure,
    handle_lamp_success,
    is_lamp_in_backoff,
    should_respect_dnd,
)
from sunrise_sunset_controller import calculate_phase
from weather_cache import evaluate_day_darkness

UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def dt(hour, minute=0, second=0) -> datetime:
    return datetime(2024, 6, 15, hour, minute, second, tzinfo=UTC)


def cfg(**kwargs) -> Config:
    c = Config()
    for k, v in kwargs.items():
        setattr(c, k, v)
    return c


def empty_state() -> dict:
    return {
        "weather_cache": None,
        "last_applied": None,
        "last_daytime_toggle_at": None,
        "do_not_disturb_until": None,
        "dnd_scope": None,
        "late_night_override": None,
        "party_mode": {"active": False},
        "lamp_failure_state": {
            "consecutive_failures": 0,
            "last_failure_at": None,
            "last_failure_type": None,
            "next_retry_at": None,
        },
        "weather_failure_state": {
            "consecutive_failures": 0,
            "last_failure_at": None,
            "next_retry_at": None,
        },
        "last_error": None,
    }


def weather_mock(sunrise_hour=5, sunrise_min=30, sunset_hour=19, sunset_min=0):
    """Return a minimal OpenWeatherLight stand-in with fixed times."""
    w = MagicMock()
    w.get_sunrise_dt.return_value = dt(sunrise_hour, sunrise_min)
    w.get_adjusted_sunset.return_value = dt(sunset_hour, sunset_min)
    return w


# Default config (times chosen for easy arithmetic):
# morning_latest_start=06:00, full_morning_time=07:00
# force_evening_time=21:00, night_full_time=22:00, hard_cutoff_time=23:00
DEFAULT_CFG = Config()


# ---------------------------------------------------------------------------
# Phase calculation
# ---------------------------------------------------------------------------

class TestCalculatePhase:
    def test_phase_timeline(self):
        """Standard time-based phases without weather or override state."""
        cases = [
            (4,  0,  "pre_morning"),        # before morning ramp
            (6,  0,  "morning_ramp"),        # at morning_latest_start
            (6,  30, "morning_ramp"),        # mid ramp
            (7,  0,  "day"),                 # full_morning_time; no weather → adj_sunset=21:00
            (14, 0,  "day"),                 # midday
            (20, 59, "day"),                 # just before force_evening
            (21, 0,  "night_ramp"),          # no weather → adj_sunset==force_evening → evening_ramp skipped
            (22, 0,  "hard_cutoff_ramp"),
            (23, 0,  "off"),                 # at hard_cutoff_time
            (23, 30, "off"),                 # after hard_cutoff
        ]
        for hour, minute, expected in cases:
            result = calculate_phase(dt(hour, minute), None, DEFAULT_CFG, empty_state())
            assert result == expected, f"at {hour:02d}:{minute:02d}: expected {expected!r}, got {result!r}"

    def test_phase_with_weather(self):
        """Phases that change when weather shifts sunrise/sunset."""
        cases = [
            (19, 30, weather_mock(sunset_hour=19),                   "evening_ramp",  "early sunset triggers evening_ramp"),
            (5,  45, weather_mock(sunrise_hour=5, sunrise_min=30),   "morning_ramp",  "ramp follows early sunrise"),
            (5,  0,  weather_mock(sunrise_hour=5, sunrise_min=30),   "pre_morning",   "before early sunrise is pre_morning"),
        ]
        for hour, minute, weather, expected, label in cases:
            result = calculate_phase(dt(hour, minute), weather, DEFAULT_CFG, empty_state())
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"

    def test_phase_with_state(self):
        """Phases driven by override state (party, late-night, expiry)."""
        party = {**empty_state(), "party_mode": {"active": True, "ends_at": dt(15, 0).isoformat()}}
        expired_party = {**empty_state(), "party_mode": {"active": True, "ends_at": dt(13, 0).isoformat()}}
        late_night = {**empty_state(), "late_night_override": {"started_at": dt(23, 5).isoformat(), "until": dt(23, 59).isoformat()}}
        expired_late = {**empty_state(), "late_night_override": {"started_at": dt(23, 0).isoformat(), "until": dt(23, 15).isoformat()}}

        cases = [
            (14, 0,  party,          "party_mode",          "party mode active"),
            (6,  15, party,          "morning_ramp",        "morning_ramp beats active party"),
            (23, 30, late_night,     "late_night_override", "late_night_override active"),
            (23, 30, expired_late,   "off",                 "expired late_night → off"),
            (14, 0,  expired_party,  "day",                 "expired party → day"),
        ]
        for hour, minute, state, expected, label in cases:
            result = calculate_phase(dt(hour, minute), None, DEFAULT_CFG, state)
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Two-stage morning ramp profile
# ---------------------------------------------------------------------------

class TestMorningRampProfile:
    """Verify _morning_ramp_profile via calculate_target_profile."""

    def _profile_at(self, t_frac: float) -> LightProfile:
        ramp_start = dt(6, 0)
        ramp_end   = dt(7, 0)
        total = (ramp_end - ramp_start).total_seconds()
        now   = ramp_start + timedelta(seconds=total * t_frac)
        return calculate_target_profile("morning_ramp", now, None, DEFAULT_CFG, empty_state())

    def test_stage_endpoints(self):
        """Start, stage-1 boundary, and end match their reference profiles."""
        cases = [
            (0.0, SUNRISE_START_PROFILE, "t=0 start"),
            (0.8, SUNRISE_END_PROFILE,   "t=0.8 stage boundary"),
        ]
        for t, ref, label in cases:
            p = self._profile_at(t)
            assert p.mode == ref.mode,             f"{label}: mode"
            assert p.hue == ref.hue,               f"{label}: hue"
            assert p.saturation == ref.saturation, f"{label}: saturation"
            assert p.brightness == ref.brightness, f"{label}: brightness"

        end = self._profile_at(1.0)
        assert end.mode == MORNING_PROFILE.mode,             "t=1 end: mode"
        assert end.color_temp == MORNING_PROFILE.color_temp, "t=1 end: color_temp"
        assert end.brightness == MORNING_PROFILE.brightness, "t=1 end: brightness"

    def test_stage2_mode_is_ct(self):
        assert self._profile_at(0.9).mode == "ct", "stage 2 (t>0.8) must snap to CT mode"


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

class TestInterpolation:
    def test_fade_to_and_from_off(self):
        """Fading to OFF holds source color; fading from OFF snaps to target color."""
        src = LightProfile(mode="hsb", hue=120, saturation=80, brightness=60)
        r = interpolate_profiles(src, OFF_PROFILE, 0.5)
        assert r.hue == 120 and r.saturation == 80 and r.brightness == 30, "fade to off"

        tgt = LightProfile(mode="hsb", hue=200, saturation=70, brightness=80)
        r = interpolate_profiles(OFF_PROFILE, tgt, 0.5)
        assert r.hue == 200 and r.saturation == 70 and r.brightness == 40, "fade from off"

    def test_mode_lerp(self):
        """Cross-mode snaps to target; same-mode lerps all fields."""
        # cross-mode: CT → HSB snaps to HSB, lerps brightness
        ct  = LightProfile(mode="ct",  color_temp=3000, brightness=80)
        hsb = LightProfile(mode="hsb", hue=30, saturation=50, brightness=60)
        r = interpolate_profiles(ct, hsb, 0.5)
        assert r.mode == "hsb" and r.hue == 30 and r.saturation == 50 and r.brightness == 70, "cross-mode ct→hsb"

        # ct → ct lerps both fields
        a = LightProfile(mode="ct", color_temp=2000, brightness=40)
        b = LightProfile(mode="ct", color_temp=6000, brightness=100)
        r = interpolate_profiles(a, b, 0.5)
        assert r.mode == "ct" and r.color_temp == 4000 and r.brightness == 70, "ct→ct lerp"

        # hsb → hsb lerps brightness
        a = LightProfile(mode="hsb", hue=0, saturation=0, brightness=0)
        b = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
        assert interpolate_profiles(a, b, 0.3).brightness == 30, "hsb brightness lerp"

    def test_lerp_hue_shortest_path(self):
        """Hue interpolation always takes the shortest arc around the colour wheel."""
        assert lerp_hue(350, 10,  0.5) == 0,  "350→10 wraps forward (+20°)"
        assert lerp_hue(20,  100, 0.5) == 60, "20→100 no wrap"
        assert lerp_hue(10,  350, 0.5) == 0,  "10→350 wraps backward (−20°)"


# ---------------------------------------------------------------------------
# Manual override detection
# ---------------------------------------------------------------------------

class TestDetectManualOverride:
    def test_detect_manual_override(self):
        cases = [
            ({"on": True},  {},               "day",          "none",               "no last_applied"),
            ({"on": True},  {"power": True},  "day",          "none",               "power unchanged"),
            ({"on": False}, {"power": True},  "morning_ramp", "manual_off",         "user turned lamp off"),
            ({"on": True},  {"power": False}, "day",          "manual_on",          "user turned lamp on (day)"),
            ({"on": True},  {"power": False}, "pre_morning",  "manual_on",          "user turned lamp on (pre_morning)"),
            ({"on": True},  {"power": False}, "off",          "late_night_trigger", "manual on after cutoff"),
        ]
        for light_state, last_applied, phase, expected, label in cases:
            result = detect_manual_override(light_state, last_applied, phase)
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"


# ---------------------------------------------------------------------------
# DND management
# ---------------------------------------------------------------------------

class TestDND:
    def test_apply_dnd_scope(self):
        """morning_ramp → morning_ramp scope; evening/night ramp → overnight scope."""
        state = empty_state()
        apply_dnd_flag(state, "morning_ramp", dt(6, 15), DEFAULT_CFG)
        assert state["dnd_scope"] == "morning_ramp", "morning_ramp scope"
        assert datetime.fromisoformat(state["do_not_disturb_until"]).hour == 7, "clears at 07:00"

        for phase in ["evening_ramp", "night_ramp"]:
            state = empty_state()
            apply_dnd_flag(state, phase, dt(19, 30), DEFAULT_CFG)
            assert state["dnd_scope"] == "overnight", f"{phase} → overnight scope"
            assert datetime.fromisoformat(state["do_not_disturb_until"]).hour == 7, f"{phase} clears at 07:00 tomorrow"

        state = empty_state()
        apply_dnd_flag(state, "day", dt(14, 0), DEFAULT_CFG)
        assert state["dnd_scope"] is None, "day phase must not set DND"

    def test_should_respect_dnd(self):
        state = empty_state()
        state["do_not_disturb_until"] = dt(8, 0).isoformat()
        assert should_respect_dnd(state, dt(7, 0)) is True, "DND active before expiry"
        state["do_not_disturb_until"] = dt(6, 0).isoformat()
        assert should_respect_dnd(state, dt(7, 0)) is False, "DND expired"

    def test_clear_dnd_if_expired(self):
        """morning_ramp scope clears at full_morning_time; overnight clears at ramp start."""
        # morning_ramp: not yet expired
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "morning_ramp"
        clear_dnd_if_expired(state, dt(6, 30), DEFAULT_CFG, None)
        assert state["dnd_scope"] == "morning_ramp", "should not clear before full_morning_time"

        # morning_ramp: expired
        state["dnd_scope"] = "morning_ramp"
        clear_dnd_if_expired(state, dt(7, 1), DEFAULT_CFG, None)
        assert state["dnd_scope"] is None and state["do_not_disturb_until"] is None, "clears after full_morning_time"

        # overnight: clears at sunrise
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "overnight"
        clear_dnd_if_expired(state, dt(5, 45), DEFAULT_CFG, weather_mock(sunrise_hour=5, sunrise_min=30))
        assert state["dnd_scope"] is None, "overnight clears at sunrise"


# ---------------------------------------------------------------------------
# Oscillation lockout
# ---------------------------------------------------------------------------

class TestOscillationLockout:
    def test_within_lockout_returns_cached_power(self):
        """Within the lockout window, last known power is returned without re-evaluating."""
        for cached_power in [True, False]:
            state = empty_state()
            state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=10)).isoformat()
            state["last_applied"] = {"power": cached_power}
            result = evaluate_day_darkness(None, state, dt(14, 0), DEFAULT_CFG)
            assert result is cached_power, f"cached_power={cached_power}: got {result}"

    def test_outside_lockout_re_evaluates_weather(self):
        """After lockout expires (or no toggle recorded), weather is re-evaluated."""
        for minutes_ago, has_toggle in [(45, True), (0, False)]:
            state = empty_state()
            if has_toggle:
                state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=minutes_ago)).isoformat()
                state["last_applied"] = {"power": True}
            w = MagicMock()
            w.is_dark_outside.return_value = True
            evaluate_day_darkness(w, state, dt(14, 0), DEFAULT_CFG)
            w.is_dark_outside.assert_called_once(), f"minutes_ago={minutes_ago}: weather not re-evaluated"
            w.reset_mock()


# ---------------------------------------------------------------------------
# Late-night override profile
# ---------------------------------------------------------------------------

class TestLateNightOverride:
    def test_profile_fades_from_late_night_to_off(self):
        """Profile starts at LATE_NIGHT_PROFILE and dims to near-zero by the end."""
        started = dt(23, 5)
        until   = started + timedelta(minutes=120)
        state   = {**empty_state(), "late_night_override": {"started_at": started.isoformat(), "until": until.isoformat()}}

        p_start = calculate_target_profile("late_night_override", started, None, DEFAULT_CFG, state)
        assert p_start.mode == LATE_NIGHT_PROFILE.mode,           "start: mode"
        assert p_start.brightness == LATE_NIGHT_PROFILE.brightness, "start: brightness"

        p_mid = calculate_target_profile("late_night_override", started + timedelta(minutes=60), None, DEFAULT_CFG, state)
        assert p_mid.brightness < LATE_NIGHT_PROFILE.brightness, "midpoint dimmer than start"
        assert p_mid.brightness > 0,                             "midpoint not yet off"

    def test_morning_ramp_overrides_late_night(self):
        state = {**empty_state(), "late_night_override": {"started_at": dt(23, 30).isoformat(), "until": dt(8, 0).isoformat()}}
        assert calculate_phase(dt(6, 15), None, DEFAULT_CFG, state) == "morning_ramp"

    def test_late_night_override_cleared_when_morning_ramp_starts(self):
        from sunrise_sunset_controller import _run

        state_store = {}

        def fake_load_state():
            s = empty_state()
            s["late_night_override"] = {"started_at": dt(23, 30).isoformat(), "until": dt(8, 0).isoformat()}
            return s

        fake_light = MagicMock()
        fake_light.get_full_state.return_value = {"on": False}
        fake_light.set_hsb.return_value = True
        fake_light.set_color_temp_and_brightness.return_value = True
        fake_light.power_on.return_value = True
        fake_light.power_off.return_value = True

        with (
            patch("sunrise_sunset_controller.load_state", fake_load_state),
            patch("sunrise_sunset_controller.save_state", lambda s: state_store.update({"state": s})),
            patch("sunrise_sunset_controller.load_config", return_value=DEFAULT_CFG),
            patch("sunrise_sunset_controller.setup_logging"),
            patch("sunrise_sunset_controller.get_weather", return_value=None),
            patch("sunrise_sunset_controller.nanoleafLight", return_value=fake_light),
        ):
            _run(dt(6, 15))

        assert state_store["state"]["late_night_override"] is None


# ---------------------------------------------------------------------------
# Party mode override handling
# ---------------------------------------------------------------------------

class TestPartyModeOverride:
    def _run_with_state(self, state_in, now_dt, lamp_on=False):
        """Run _run() with full mocking, return the saved state."""
        from sunrise_sunset_controller import _run

        saved = {}
        fake_light = MagicMock()
        fake_light.get_full_state.return_value = {"on": lamp_on}
        fake_light.set_hsb.return_value = True
        fake_light.set_color_temp_and_brightness.return_value = True
        fake_light.power_on.return_value = True
        fake_light.power_off.return_value = True

        with (
            patch("sunrise_sunset_controller.load_state", return_value=state_in),
            patch("sunrise_sunset_controller.save_state", lambda s: saved.update({"state": s})),
            patch("sunrise_sunset_controller.load_config", return_value=DEFAULT_CFG),
            patch("sunrise_sunset_controller.setup_logging"),
            patch("sunrise_sunset_controller.get_weather", return_value=None),
            patch("sunrise_sunset_controller.nanoleafLight", return_value=fake_light),
        ):
            _run(now_dt)

        return saved.get("state")

    def test_manual_off_during_party_clears_party_no_dnd(self):
        state = empty_state()
        state["party_mode"] = {
            "active": True,
            "ends_at": dt(23, 30).isoformat(),
            "fade_minutes": 30,
            "profile": {"mode": "hsb", "hue": 280, "saturation": 90, "brightness": 100},
        }
        state["last_applied"] = {"power": True, "phase": "party_mode"}

        result = self._run_with_state(state, dt(22, 0), lamp_on=False)
        assert result["party_mode"]["active"] is False, "party must be cleared"
        assert result["do_not_disturb_until"] is None, "manual-off during party must not set DND"

    def test_party_cleared_when_morning_ramp_starts(self):
        state = empty_state()
        state["party_mode"] = {
            "active": True,
            "ends_at": dt(8, 0).isoformat(),
            "fade_minutes": 0,
            "profile": {"mode": "hsb", "hue": 280, "saturation": 90, "brightness": 100},
        }

        result = self._run_with_state(state, dt(6, 15), lamp_on=True)
        assert result["party_mode"]["active"] is False, "party must be cleared at morning ramp"
        assert result["last_applied"]["phase"] == "morning_ramp"


# ---------------------------------------------------------------------------
# last_applied schema
# ---------------------------------------------------------------------------

class TestLastAppliedSchema:
    def test_last_applied_uses_timestamp_key(self):
        from sunrise_sunset_controller import _run

        saved = {}
        fake_light = MagicMock()
        fake_light.get_full_state.return_value = {"on": False}
        fake_light.set_hsb.return_value = True
        fake_light.set_color_temp_and_brightness.return_value = True
        fake_light.power_on.return_value = True
        fake_light.power_off.return_value = True

        with (
            patch("sunrise_sunset_controller.load_state", return_value=empty_state()),
            patch("sunrise_sunset_controller.save_state", lambda s: saved.update({"state": s})),
            patch("sunrise_sunset_controller.load_config", return_value=DEFAULT_CFG),
            patch("sunrise_sunset_controller.setup_logging"),
            patch("sunrise_sunset_controller.get_weather", return_value=None),
            patch("sunrise_sunset_controller.nanoleafLight", return_value=fake_light),
        ):
            _run(dt(14, 0))

        la = saved["state"]["last_applied"]
        assert "timestamp" in la and "at" not in la, "key must be 'timestamp', not 'at'"
        assert all(k in la for k in ("phase", "power", "profile")), "missing required keys"


# ---------------------------------------------------------------------------
# Weather backoff
# ---------------------------------------------------------------------------

class TestWeatherBackoff:
    def test_failure_increments_counter(self):
        from weather_cache import get_weather
        state = empty_state()
        with patch("weather_cache.OpenWeatherLight", side_effect=Exception("API down")):
            get_weather(state, dt(10, 0), DEFAULT_CFG)
        f = state["weather_failure_state"]
        assert f["consecutive_failures"] == 1 and f["next_retry_at"] is not None

    def test_should_refresh_weather(self):
        """Backoff blocks, stale/absent cache triggers, fresh cache skips, anchor overrides backoff."""
        from weather_cache import should_refresh_weather

        # in backoff, non-anchor → no refresh
        state = empty_state()
        state["weather_failure_state"]["consecutive_failures"] = 2
        state["weather_failure_state"]["next_retry_at"] = (dt(14, 0) + timedelta(minutes=20)).isoformat()
        assert should_refresh_weather(state, dt(14, 5), DEFAULT_CFG) is False, "in backoff, non-anchor"

        # no backoff, no cache → refresh
        assert should_refresh_weather(empty_state(), dt(10, 0), DEFAULT_CFG) is True, "no cache, no backoff"

        # fresh cache → no refresh
        fresh = empty_state()
        fresh["weather_cache"] = {"fetched_at": dt(10, 0).isoformat(), "raw_data": {}}
        assert should_refresh_weather(fresh, dt(11, 0), DEFAULT_CFG) is False, "fresh cache"

    def test_anchor_time_forces_refresh_even_in_backoff(self):
        """14:00 is a configured anchor — must refresh even when in backoff."""
        from weather_cache import should_refresh_weather
        state = empty_state()
        state["weather_failure_state"]["consecutive_failures"] = 3
        state["weather_failure_state"]["next_retry_at"] = (dt(14, 0) + timedelta(hours=1)).isoformat()
        assert should_refresh_weather(state, dt(14, 0), DEFAULT_CFG) is True


# ---------------------------------------------------------------------------
# Lamp backoff
# ---------------------------------------------------------------------------

class TestLampBackoff:
    def test_backoff_detection(self):
        """is_lamp_in_backoff: True when retry is in the future, False otherwise."""
        state = empty_state()
        state["lamp_failure_state"]["next_retry_at"] = dt(14, 30).isoformat()
        assert is_lamp_in_backoff(state, dt(14, 0)) is True, "retry in future → in backoff"

        state["lamp_failure_state"]["next_retry_at"] = dt(13, 0).isoformat()
        assert is_lamp_in_backoff(state, dt(14, 0)) is False, "retry passed → not in backoff"

        state["lamp_failure_state"]["next_retry_at"] = None
        assert is_lamp_in_backoff(state, dt(14, 0)) is False, "no retry → not in backoff"

    def test_handle_lamp_failure(self):
        state = empty_state()
        handle_lamp_failure(state, dt(14, 0), DEFAULT_CFG, ConnectionError("unreachable"))
        f = state["lamp_failure_state"]
        assert f["consecutive_failures"] == 1,            "failure counter incremented"
        assert f["last_failure_type"] == "ConnectionError", "exception type recorded"
        retry = datetime.fromisoformat(f["next_retry_at"])
        assert (retry - dt(14, 0)).total_seconds() == 5 * 60, "first failure → 5-min backoff"

    def test_handle_lamp_success_resets_state(self):
        state = empty_state()
        state["lamp_failure_state"]["consecutive_failures"] = 3
        state["lamp_failure_state"]["next_retry_at"] = dt(14, 30).isoformat()
        handle_lamp_success(state)
        f = state["lamp_failure_state"]
        assert f["consecutive_failures"] == 0 and f["next_retry_at"] is None


# ---------------------------------------------------------------------------
# describe_color
# ---------------------------------------------------------------------------

class TestDescribeColor:
    def test_describe_color(self):
        cases = [
            (LightProfile(mode="hsb", brightness=0),                              "off",           None,        "brightness=0 → 'off'"),
            (LightProfile(mode="ct",  color_temp=6000, brightness=100),           "daylight white", "full",     "CT 6000K full"),
            (LightProfile(mode="ct",  color_temp=2500, brightness=20),            "warm white",     "dim",      "CT 2500K dim"),
            (NIGHT_PROFILE,                                                        "amber",          "dim",      "night profile"),
            (LightProfile(mode="hsb", hue=280, saturation=90, brightness=100),    "purple",         "full",     "party purple"),
            (LightProfile(mode="hsb", hue=120, saturation=5,  brightness=50),     "near white",     None,       "low saturation → near white"),
        ]
        for profile, expected_color, expected_brightness, label in cases:
            result = describe_color(profile)
            if expected_color == "off":
                assert result == "off", f"{label}: expected 'off', got {result!r}"
            else:
                assert expected_color in result, f"{label}: expected {expected_color!r} in {result!r}"
            if expected_brightness:
                assert expected_brightness in result, f"{label}: expected brightness {expected_brightness!r} in {result!r}"

    def test_describe_daytime_on(self):
        result = describe_color(DAYTIME_ON_PROFILE)
        assert "orange" in result or "amber" in result, f"expected orange or amber, got {result!r}"
        assert "moderate" in result, f"expected moderate brightness, got {result!r}"
