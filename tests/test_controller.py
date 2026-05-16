"""Unit tests for controller logic: phase calculation, profiles, interpolation,
manual overrides, DND, oscillation lockout, late-night override, backoff,
and describe_color.
"""

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock
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


# Default config used throughout (times chosen for easy arithmetic)
# morning_latest_start=06:00, full_morning_time=07:00
# force_evening_time=21:00, night_full_time=22:00, hard_cutoff_time=23:00
DEFAULT_CFG = Config()


# ---------------------------------------------------------------------------
# Phase calculation
# ---------------------------------------------------------------------------

class TestCalculatePhase:
    @pytest.mark.parametrize("hour,minute,expected", [
        (4,  0,  "pre_morning"),       # before morning ramp
        (6,  0,  "morning_ramp"),      # at morning_latest_start
        (6,  30, "morning_ramp"),      # mid ramp
        (7,  0,  "day"),               # at full_morning_time, no weather → adj_sunset=21:00
        (14, 0,  "day"),               # midday
        (20, 59, "day"),               # just before force_evening
        (21, 0,  "night_ramp"),        # no weather → adj_sunset==force_evening → evening_ramp skipped
        (22, 0,  "hard_cutoff_ramp"),
        (23, 0,  "off"),               # at hard_cutoff_time
        (23, 30, "off"),               # after hard_cutoff
    ])
    def test_standard_phases_no_weather(self, hour, minute, expected):
        assert calculate_phase(dt(hour, minute), None, DEFAULT_CFG, empty_state()) == expected

    def test_evening_ramp_with_early_sunset(self):
        # sunset at 19:00 → evening_ramp from 19:00 to 21:00
        w = weather_mock(sunset_hour=19)
        assert calculate_phase(dt(19, 30), w, DEFAULT_CFG, empty_state()) == "evening_ramp"

    def test_morning_ramp_follows_early_sunrise(self):
        # weather sunrise at 05:30 — ramp starts before morning_latest_start
        w = weather_mock(sunrise_hour=5, sunrise_min=30)
        assert calculate_phase(dt(5, 45), w, DEFAULT_CFG, empty_state()) == "morning_ramp"

    def test_pre_morning_before_weather_sunrise(self):
        w = weather_mock(sunrise_hour=5, sunrise_min=30)
        assert calculate_phase(dt(5, 0), w, DEFAULT_CFG, empty_state()) == "pre_morning"

    def test_party_mode_active(self):
        state = empty_state()
        state["party_mode"] = {
            "active": True,
            "ends_at": dt(15, 0).isoformat(),
        }
        assert calculate_phase(dt(14, 0), None, DEFAULT_CFG, state) == "party_mode"

    def test_morning_ramp_wins_over_party(self):
        state = empty_state()
        state["party_mode"] = {
            "active": True,
            "ends_at": dt(15, 0).isoformat(),
        }
        # 06:15 is inside morning_ramp window — must win
        assert calculate_phase(dt(6, 15), None, DEFAULT_CFG, state) == "morning_ramp"

    def test_late_night_override_active(self):
        state = empty_state()
        state["late_night_override"] = {
            "started_at": dt(23, 5).isoformat(),
            "until": dt(23, 59).isoformat(),
        }
        assert calculate_phase(dt(23, 30), None, DEFAULT_CFG, state) == "late_night_override"

    def test_late_night_override_expired_returns_off(self):
        state = empty_state()
        state["late_night_override"] = {
            "started_at": dt(23, 0).isoformat(),
            "until": dt(23, 15).isoformat(),
        }
        assert calculate_phase(dt(23, 30), None, DEFAULT_CFG, state) == "off"

    def test_party_mode_expired_returns_day(self):
        state = empty_state()
        state["party_mode"] = {
            "active": True,
            "ends_at": dt(13, 0).isoformat(),  # already expired at 14:00
        }
        assert calculate_phase(dt(14, 0), None, DEFAULT_CFG, state) == "day"


# ---------------------------------------------------------------------------
# Two-stage morning ramp profile
# ---------------------------------------------------------------------------

class TestMorningRampProfile:
    """Verify _morning_ramp_profile via calculate_target_profile."""

    def _profile_at(self, t_frac: float) -> LightProfile:
        """Return the morning-ramp profile at fraction t of the ramp window."""
        ramp_start = dt(6, 0)
        ramp_end = dt(7, 0)
        total = (ramp_end - ramp_start).total_seconds()
        now = ramp_start + timedelta(seconds=total * t_frac)
        return calculate_target_profile("morning_ramp", now, None, DEFAULT_CFG, empty_state())

    def test_at_start_matches_sunrise_start(self):
        profile = self._profile_at(0.0)
        assert profile.mode == SUNRISE_START_PROFILE.mode
        assert profile.hue == SUNRISE_START_PROFILE.hue
        assert profile.saturation == SUNRISE_START_PROFILE.saturation
        assert profile.brightness == SUNRISE_START_PROFILE.brightness

    def test_at_stage_boundary_matches_sunrise_end(self):
        profile = self._profile_at(0.8)
        assert profile.mode == SUNRISE_END_PROFILE.mode
        assert profile.hue == SUNRISE_END_PROFILE.hue
        assert profile.saturation == SUNRISE_END_PROFILE.saturation
        assert profile.brightness == SUNRISE_END_PROFILE.brightness

    def test_at_end_matches_morning(self):
        profile = self._profile_at(1.0)
        assert profile.mode == MORNING_PROFILE.mode
        assert profile.color_temp == MORNING_PROFILE.color_temp
        assert profile.brightness == MORNING_PROFILE.brightness

    def test_stage1_brightness_increases(self):
        p0 = self._profile_at(0.0)
        p4 = self._profile_at(0.4)
        assert p4.brightness > p0.brightness

    def test_stage2_mode_is_ct(self):
        # stage 2 (t > 0.8) snaps to MORNING which is CT
        p = self._profile_at(0.9)
        assert p.mode == "ct"


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

class TestInterpolation:
    def test_fade_to_off_holds_source_color(self):
        src = LightProfile(mode="hsb", hue=120, saturation=80, brightness=60)
        result = interpolate_profiles(src, OFF_PROFILE, 0.5)
        assert result.hue == 120
        assert result.saturation == 80
        assert result.brightness == 30  # midpoint between 60 and 0

    def test_fade_from_off_snaps_to_target_color(self):
        target = LightProfile(mode="hsb", hue=200, saturation=70, brightness=80)
        result = interpolate_profiles(OFF_PROFILE, target, 0.5)
        assert result.hue == 200
        assert result.saturation == 70
        assert result.brightness == 40  # midpoint between 0 and 80

    def test_cross_mode_snaps_to_target(self):
        ct_src = LightProfile(mode="ct", color_temp=3000, brightness=80)
        hsb_tgt = LightProfile(mode="hsb", hue=30, saturation=50, brightness=60)
        result = interpolate_profiles(ct_src, hsb_tgt, 0.5)
        assert result.mode == "hsb"
        assert result.hue == 30
        assert result.saturation == 50
        assert result.brightness == 70  # midpoint 80→60

    def test_ct_to_ct_lerps(self):
        a = LightProfile(mode="ct", color_temp=2000, brightness=40)
        b = LightProfile(mode="ct", color_temp=6000, brightness=100)
        result = interpolate_profiles(a, b, 0.5)
        assert result.mode == "ct"
        assert result.color_temp == 4000
        assert result.brightness == 70

    def test_hsb_to_hsb_lerps_brightness(self):
        a = LightProfile(mode="hsb", hue=0, saturation=0, brightness=0)
        b = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
        result = interpolate_profiles(a, b, 0.3)
        assert result.brightness == 30

    def test_lerp_hue_shortest_path_wrap(self):
        # 350 → 10: shortest path is +20°, not -340°
        assert lerp_hue(350, 10, 0.5) == 0

    def test_lerp_hue_no_wrap_needed(self):
        assert lerp_hue(20, 100, 0.5) == 60

    def test_lerp_hue_reverse_wrap(self):
        # 10 → 350: shortest path is -20° (backward)
        assert lerp_hue(10, 350, 0.5) == 0


# ---------------------------------------------------------------------------
# Manual override detection
# ---------------------------------------------------------------------------

class TestDetectManualOverride:
    def test_no_last_applied_returns_none(self):
        assert detect_manual_override({"on": True}, {}, "day") == "none"

    def test_power_unchanged_returns_none(self):
        assert detect_manual_override(
            {"on": True}, {"power": True}, "day"
        ) == "none"

    def test_manual_off(self):
        assert detect_manual_override(
            {"on": False}, {"power": True}, "morning_ramp"
        ) == "manual_off"

    def test_manual_on(self):
        assert detect_manual_override(
            {"on": True}, {"power": False}, "day"
        ) == "manual_on"

    def test_late_night_trigger(self):
        assert detect_manual_override(
            {"on": True}, {"power": False}, "off"
        ) == "late_night_trigger"

    def test_manual_on_not_late_night_in_non_off_phase(self):
        assert detect_manual_override(
            {"on": True}, {"power": False}, "pre_morning"
        ) == "manual_on"


# ---------------------------------------------------------------------------
# DND management
# ---------------------------------------------------------------------------

class TestDND:
    def test_apply_dnd_morning_ramp_scope(self):
        state = empty_state()
        apply_dnd_flag(state, "morning_ramp", dt(6, 15), DEFAULT_CFG)
        assert state["dnd_scope"] == "morning_ramp"
        dnd_until = datetime.fromisoformat(state["do_not_disturb_until"])
        assert dnd_until.hour == 7 and dnd_until.minute == 0

    def test_apply_dnd_evening_ramp_scope(self):
        state = empty_state()
        apply_dnd_flag(state, "evening_ramp", dt(19, 30), DEFAULT_CFG)
        assert state["dnd_scope"] == "overnight"
        dnd_until = datetime.fromisoformat(state["do_not_disturb_until"])
        # should clear at full_morning_time tomorrow (7:00)
        assert dnd_until.hour == 7 and dnd_until.minute == 0

    def test_apply_dnd_night_ramp_scope(self):
        state = empty_state()
        apply_dnd_flag(state, "night_ramp", dt(21, 30), DEFAULT_CFG)
        assert state["dnd_scope"] == "overnight"

    def test_should_respect_dnd_active(self):
        state = empty_state()
        state["do_not_disturb_until"] = dt(8, 0).isoformat()
        assert should_respect_dnd(state, dt(7, 0)) is True

    def test_should_respect_dnd_expired(self):
        state = empty_state()
        state["do_not_disturb_until"] = dt(6, 0).isoformat()
        assert should_respect_dnd(state, dt(7, 0)) is False

    def test_clear_dnd_morning_ramp_scope_after_full_morning(self):
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "morning_ramp"
        clear_dnd_if_expired(state, dt(7, 1), DEFAULT_CFG, None)
        assert state["do_not_disturb_until"] is None
        assert state["dnd_scope"] is None

    def test_clear_dnd_morning_ramp_scope_not_yet(self):
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "morning_ramp"
        clear_dnd_if_expired(state, dt(6, 30), DEFAULT_CFG, None)
        assert state["dnd_scope"] == "morning_ramp"  # not cleared yet

    def test_clear_dnd_overnight_scope_with_weather(self):
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "overnight"
        # sunrise at 05:30 → morning_ramp_start = min(05:30, 06:00) = 05:30
        w = weather_mock(sunrise_hour=5, sunrise_min=30)
        clear_dnd_if_expired(state, dt(5, 45), DEFAULT_CFG, w)
        assert state["dnd_scope"] is None

    def test_day_phase_does_not_set_dnd(self):
        state = empty_state()
        apply_dnd_flag(state, "day", dt(14, 0), DEFAULT_CFG)
        assert state["dnd_scope"] is None
        assert state["do_not_disturb_until"] is None


# ---------------------------------------------------------------------------
# Oscillation lockout
# ---------------------------------------------------------------------------

class TestOscillationLockout:
    def test_within_lockout_returns_cached_power(self):
        state = empty_state()
        state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=10)).isoformat()
        state["last_applied"] = {"power": True}
        # within 30-min lockout → return cached True
        assert evaluate_day_darkness(None, state, dt(14, 0), DEFAULT_CFG) is True

    def test_within_lockout_cached_off(self):
        state = empty_state()
        state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=5)).isoformat()
        state["last_applied"] = {"power": False}
        assert evaluate_day_darkness(None, state, dt(14, 0), DEFAULT_CFG) is False

    def test_outside_lockout_re_evaluates_weather(self):
        state = empty_state()
        state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=45)).isoformat()
        state["last_applied"] = {"power": True}
        w = MagicMock()
        w.is_dark_outside.return_value = False
        result = evaluate_day_darkness(w, state, dt(14, 0), DEFAULT_CFG)
        w.is_dark_outside.assert_called_once()
        assert result is False

    def test_no_lockout_without_toggle_timestamp(self):
        state = empty_state()
        w = MagicMock()
        w.is_dark_outside.return_value = True
        result = evaluate_day_darkness(w, state, dt(14, 0), DEFAULT_CFG)
        w.is_dark_outside.assert_called_once()
        assert result is True


# ---------------------------------------------------------------------------
# Late-night override profile
# ---------------------------------------------------------------------------

class TestLateNightOverride:
    def test_trigger_sets_state(self):
        # Simulated: the controller detects late_night_trigger and sets state
        state = empty_state()
        now = dt(23, 10)
        state["late_night_override"] = {
            "started_at": now.isoformat(),
            "until": (now + timedelta(minutes=120)).isoformat(),
        }
        assert calculate_phase(dt(23, 20), None, DEFAULT_CFG, state) == "late_night_override"

    def test_profile_at_start_matches_late_night(self):
        state = empty_state()
        started = dt(23, 5)
        until = dt(23, 5) + timedelta(minutes=120)
        state["late_night_override"] = {
            "started_at": started.isoformat(),
            "until": until.isoformat(),
        }
        p = calculate_target_profile("late_night_override", started, None, DEFAULT_CFG, state)
        assert p.mode == LATE_NIGHT_PROFILE.mode
        assert p.brightness == LATE_NIGHT_PROFILE.brightness

    def test_profile_midpoint_is_dimmer(self):
        state = empty_state()
        started = dt(23, 5)
        until = started + timedelta(minutes=120)
        mid = started + timedelta(minutes=60)
        state["late_night_override"] = {
            "started_at": started.isoformat(),
            "until": until.isoformat(),
        }
        p = calculate_target_profile("late_night_override", mid, None, DEFAULT_CFG, state)
        assert p.brightness < LATE_NIGHT_PROFILE.brightness
        assert p.brightness > 0

    def test_morning_ramp_overrides_late_night(self):
        state = empty_state()
        state["late_night_override"] = {
            "started_at": dt(23, 30).isoformat(),
            "until": dt(8, 0).isoformat(),
        }
        # morning_ramp_start = 06:00; at 06:15 morning_ramp wins
        assert calculate_phase(dt(6, 15), None, DEFAULT_CFG, state) == "morning_ramp"

    def test_late_night_override_cleared_when_morning_ramp_starts(self):
        from sunrise_sunset_controller import _run
        from unittest.mock import patch, MagicMock

        state_store = {"state": None}

        def fake_load_state():
            s = empty_state()
            s["late_night_override"] = {
                "started_at": dt(23, 30).isoformat(),
                "until": dt(8, 0).isoformat(),
            }
            return s

        def fake_save_state(s):
            state_store["state"] = s

        fake_light = MagicMock()
        fake_light.get_full_state.return_value = {"on": False}
        fake_light.set_hsb.return_value = True
        fake_light.set_color_temp_and_brightness.return_value = True
        fake_light.power_on.return_value = True
        fake_light.power_off.return_value = True

        with (
            patch("sunrise_sunset_controller.load_state", fake_load_state),
            patch("sunrise_sunset_controller.save_state", fake_save_state),
            patch("sunrise_sunset_controller.load_config", return_value=DEFAULT_CFG),
            patch("sunrise_sunset_controller.setup_logging"),
            patch("sunrise_sunset_controller.get_weather", return_value=None),
            patch("sunrise_sunset_controller.nanoleafLight", return_value=fake_light),
        ):
            _run(dt(6, 15))  # inside morning_ramp window

        assert state_store["state"]["late_night_override"] is None


# ---------------------------------------------------------------------------
# Weather backoff
# ---------------------------------------------------------------------------

class TestWeatherBackoff:
    def test_failure_increments_counter(self):
        from unittest.mock import patch
        from weather_cache import get_weather
        state = empty_state()
        with patch("weather_cache.OpenWeatherLight", side_effect=Exception("API down")):
            get_weather(state, dt(10, 0), DEFAULT_CFG)
        f = state["weather_failure_state"]
        assert f["consecutive_failures"] == 1
        assert f["next_retry_at"] is not None

    def test_backoff_blocks_non_anchor_refresh(self):
        from weather_cache import should_refresh_weather
        state = empty_state()
        failure = state["weather_failure_state"]
        failure["consecutive_failures"] = 2
        failure["next_retry_at"] = (dt(14, 0) + timedelta(minutes=20)).isoformat()
        assert should_refresh_weather(state, dt(14, 5), DEFAULT_CFG) is False

    def test_anchor_time_forces_refresh_even_in_backoff(self):
        from weather_cache import should_refresh_weather
        state = empty_state()
        failure = state["weather_failure_state"]
        failure["consecutive_failures"] = 3
        failure["next_retry_at"] = (dt(14, 0) + timedelta(hours=1)).isoformat()
        # 14:00 is an anchor time (weather_fetch_evening default)
        assert should_refresh_weather(state, dt(14, 0), DEFAULT_CFG) is True

    def test_no_backoff_and_stale_cache_triggers_refresh(self):
        from weather_cache import should_refresh_weather
        state = empty_state()
        # no failure, no cache
        assert should_refresh_weather(state, dt(10, 0), DEFAULT_CFG) is True

    def test_fresh_cache_skips_refresh(self):
        from weather_cache import should_refresh_weather
        state = empty_state()
        state["weather_cache"] = {
            "fetched_at": dt(10, 0).isoformat(),
            "raw_data": {},
        }
        # 1 hour later, cache_max_age_hours=5 → still fresh
        assert should_refresh_weather(state, dt(11, 0), DEFAULT_CFG) is False


# ---------------------------------------------------------------------------
# Lamp backoff
# ---------------------------------------------------------------------------

class TestLampBackoff:
    def test_in_backoff_when_retry_in_future(self):
        state = empty_state()
        state["lamp_failure_state"]["next_retry_at"] = dt(14, 30).isoformat()
        assert is_lamp_in_backoff(state, dt(14, 0)) is True

    def test_not_in_backoff_when_retry_passed(self):
        state = empty_state()
        state["lamp_failure_state"]["next_retry_at"] = dt(13, 0).isoformat()
        assert is_lamp_in_backoff(state, dt(14, 0)) is False

    def test_not_in_backoff_when_no_retry(self):
        state = empty_state()
        assert is_lamp_in_backoff(state, dt(14, 0)) is False

    def test_handle_lamp_failure_increments(self):
        state = empty_state()
        exc = ConnectionError("unreachable")
        handle_lamp_failure(state, dt(14, 0), DEFAULT_CFG, exc)
        f = state["lamp_failure_state"]
        assert f["consecutive_failures"] == 1
        assert f["next_retry_at"] is not None
        assert f["last_failure_type"] == "ConnectionError"

    def test_handle_lamp_failure_uses_backoff_schedule(self):
        state = empty_state()
        exc = ConnectionError("x")
        handle_lamp_failure(state, dt(14, 0), DEFAULT_CFG, exc)
        retry = datetime.fromisoformat(state["lamp_failure_state"]["next_retry_at"])
        # first failure → 5-minute backoff
        assert (retry - dt(14, 0)).total_seconds() == 5 * 60

    def test_handle_lamp_success_resets_state(self):
        state = empty_state()
        state["lamp_failure_state"]["consecutive_failures"] = 3
        state["lamp_failure_state"]["next_retry_at"] = dt(14, 30).isoformat()
        handle_lamp_success(state)
        f = state["lamp_failure_state"]
        assert f["consecutive_failures"] == 0
        assert f["next_retry_at"] is None


# ---------------------------------------------------------------------------
# describe_color
# ---------------------------------------------------------------------------

class TestDescribeColor:
    def test_off(self):
        assert describe_color(LightProfile(mode="hsb", brightness=0)) == "off"

    def test_ct_profile(self):
        p = LightProfile(mode="ct", color_temp=6000, brightness=100)
        result = describe_color(p)
        assert "daylight white" in result
        assert "full" in result

    def test_ct_warm(self):
        p = LightProfile(mode="ct", color_temp=2500, brightness=20)
        result = describe_color(p)
        assert "warm white" in result
        assert "dim" in result

    def test_hsb_night_profile(self):
        result = describe_color(NIGHT_PROFILE)
        assert "amber" in result
        assert "dim" in result

    def test_hsb_party_profile(self):
        result = describe_color(LightProfile(mode="hsb", hue=280, saturation=90, brightness=100))
        assert "purple" in result
        assert "full" in result

    def test_hsb_daytime_on(self):
        result = describe_color(DAYTIME_ON_PROFILE)
        assert "orange" in result or "amber" in result
        assert "moderate" in result

    def test_hsb_saturation_modifier(self):
        dim_sat = LightProfile(mode="hsb", hue=120, saturation=5, brightness=50)
        assert "near white" in describe_color(dim_sat)
