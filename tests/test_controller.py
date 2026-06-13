"""Unit tests for controller logic: phase calculation, profiles, interpolation,
manual overrides, DND, oscillation lockout, late-night override, backoff,
and describe_color.
"""

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from nanoleaf.color_helper import describe_color
from controller.config import (
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
from nanoleaf.interpolation import interpolate_profiles, lerp_hue
from controller.profiles import (
    calculate_effective_color_profile,
    calculate_target_profile,
)
from controller.dateTime import get_morning_ramp_start, parse_iso
from controller.state import (
    _empty_state,
    apply_dnd_flag,
    clear_dnd_if_expired,
    detect_manual_override,
    handle_lamp_failure,
    handle_lamp_success,
    is_lamp_in_backoff,
    should_respect_dnd,
)
from controller.phase import calculate_phase
from weather.weather_cache import evaluate_day_darkness

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


empty_state = _empty_state  # single source of truth in controller.state


def weather_mock(sunrise_hour=5, sunrise_min=30, sunset_hour=19, sunset_min=0):
    """Return a minimal OpenWeatherLight stand-in with fixed times."""
    w = MagicMock()
    w.get_sunrise_dt.return_value = dt(sunrise_hour, sunrise_min)
    w.get_adjusted_sunset.return_value = dt(sunset_hour, sunset_min)
    w.is_dark_outside.return_value = False  # explicit: bright outside, lamp stays off
    return w


# Default config (times chosen for easy arithmetic):
# morning_latest_start=06:00, full_morning_time=07:00
# force_evening_time=21:00, night_full_time=22:00, hard_cutoff_time=23:00
DEFAULT_CFG = Config()


def _fake_run(state_in, now_dt, lamp_on=False):
    """Run _run() with full infrastructure mocked; return the saved state dict."""
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
        patch("sunrise_sunset_controller.NanoleafLight", return_value=fake_light),
    ):
        _run(now_dt)
    return saved.get("state")


# ---------------------------------------------------------------------------
# Phase calculation
# ---------------------------------------------------------------------------

class TestCalculatePhase:
    def test_standard_timeline(self):
        """Standard time-based phases without weather or override state.
        No weather → adj_sunset == force_evening_time, so evening_ramp is never reached.
        """
        cases = [
            (4,  0,  "pre_morning",      "before morning_latest_start"),
            (6,  0,  "morning_ramp",     "at morning_latest_start boundary"),
            (6,  30, "morning_ramp",     "mid ramp"),
            (7,  0,  "day",              "at full_morning_time"),
            (14, 0,  "day",              "midday"),
            (20, 59, "day",              "just before force_evening"),
            (21, 0,  "night_ramp",       "no weather → adj_sunset == force_evening → evening_ramp skipped"),
            (22, 0,  "hard_cutoff_ramp", "at night_full_time"),
            (23, 0,  "off",              "at hard_cutoff_time"),
            (23, 30, "off",              "after hard_cutoff"),
        ]
        for hour, minute, expected, label in cases:
            result = calculate_phase(dt(hour, minute), None, DEFAULT_CFG, empty_state())
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"

    def test_weather_shifts_phase_boundaries(self):
        """Weather data moves sunrise/sunset, changing which phase is active."""
        cases = [
            (19, 30, weather_mock(sunset_hour=19), "evening_ramp",
             "early sunset at 19:00 puts 19:30 in evening_ramp"),
            (5,  45, weather_mock(sunrise_hour=5, sunrise_min=30), "morning_ramp",
             "early sunrise at 05:30 puts 05:45 in morning_ramp"),
            (5,  0,  weather_mock(sunrise_hour=5, sunrise_min=30), "pre_morning",
             "05:00 is before early sunrise at 05:30 → pre_morning"),
        ]
        for hour, minute, weather, expected, label in cases:
            result = calculate_phase(dt(hour, minute), weather, DEFAULT_CFG, empty_state())
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"

    def test_state_overrides(self):
        """Party and late-night override state gates take precedence over the timeline."""
        party         = {**empty_state(), "party_mode": {"active": True, "ends_at": dt(15, 0).isoformat()}}
        expired_party = {**empty_state(), "party_mode": {"active": True, "ends_at": dt(13, 0).isoformat()}}
        late_night    = {**empty_state(), "late_night_override": {"started_at": dt(23, 5).isoformat(), "until": dt(23, 59).isoformat()}}
        expired_late  = {**empty_state(), "late_night_override": {"started_at": dt(23, 0).isoformat(), "until": dt(23, 15).isoformat()}}

        cases = [
            (14, 0,  party,          "party_mode",          "active party at 14:00"),
            (6,  15, party,          "morning_ramp",        "morning_ramp beats active party"),
            (23, 30, late_night,     "late_night_override", "active late_night override"),
            (23, 30, expired_late,   "off",                 "expired late_night falls through to off"),
            (14, 0,  expired_party,  "day",                 "expired party falls through to day"),
        ]
        for hour, minute, state, expected, label in cases:
            result = calculate_phase(dt(hour, minute), None, DEFAULT_CFG, state)
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"


# ---------------------------------------------------------------------------
# Two-stage morning ramp profile
# ---------------------------------------------------------------------------

class TestMorningRampProfile:
    def _profile_at(self, t_frac: float) -> LightProfile:
        ramp_start = dt(6, 0)
        ramp_end   = dt(7, 0)
        total = (ramp_end - ramp_start).total_seconds()
        now   = ramp_start + timedelta(seconds=total * t_frac)
        return calculate_target_profile("morning_ramp", now, None, DEFAULT_CFG, empty_state())

    def test_two_stage_ramp(self):
        """Stage 1 (t 0→0.8): HSB warm amber from SUNRISE_START to SUNRISE_END.
        Stage 2 (t 0.8→1.0): CT snap to MORNING profile.
        """
        start = self._profile_at(0.0)
        assert start.mode == SUNRISE_START_PROFILE.mode,             "t=0 mode should match SUNRISE_START"
        assert start.hue == SUNRISE_START_PROFILE.hue,               "t=0 hue should match SUNRISE_START"
        assert start.saturation == SUNRISE_START_PROFILE.saturation, "t=0 saturation should match SUNRISE_START"
        assert start.brightness == SUNRISE_START_PROFILE.brightness, "t=0 brightness should match SUNRISE_START"

        boundary = self._profile_at(0.8)
        assert boundary.mode == SUNRISE_END_PROFILE.mode,             "t=0.8 mode should match SUNRISE_END"
        assert boundary.hue == SUNRISE_END_PROFILE.hue,               "t=0.8 hue should match SUNRISE_END"
        assert boundary.saturation == SUNRISE_END_PROFILE.saturation, "t=0.8 saturation should match SUNRISE_END"
        assert boundary.brightness == SUNRISE_END_PROFILE.brightness, "t=0.8 brightness should match SUNRISE_END"

        stage2 = self._profile_at(0.9)
        assert stage2.mode == "ct", \
            f"stage 2 (t>0.8) must snap to CT mode, got {stage2.mode!r}"

        end = self._profile_at(1.0)
        assert end.mode == MORNING_PROFILE.mode,             "t=1.0 mode should match MORNING"
        assert end.color_temp == MORNING_PROFILE.color_temp, "t=1.0 color_temp should match MORNING"
        assert end.brightness == MORNING_PROFILE.brightness, "t=1.0 brightness should match MORNING"


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

class TestInterpolation:
    def test_fade_to_and_from_off(self):
        """Fading TO off: source color held, only brightness lerps.
        Fading FROM off: target color snapped immediately, brightness lerps.
        """
        src = LightProfile(mode="hsb", hue=120, saturation=80, brightness=60)
        r = interpolate_profiles(src, OFF_PROFILE, 0.5)
        assert r.hue == 120,       f"fade-to-off at t=0.5: hue should stay 120, got {r.hue}"
        assert r.saturation == 80, f"fade-to-off at t=0.5: saturation should stay 80, got {r.saturation}"
        assert r.brightness == 30, f"fade-to-off at t=0.5: brightness should be 30, got {r.brightness}"

        tgt = LightProfile(mode="hsb", hue=200, saturation=70, brightness=80)
        r = interpolate_profiles(OFF_PROFILE, tgt, 0.5)
        assert r.hue == 200,       f"fade-from-off at t=0.5: hue should snap to 200, got {r.hue}"
        assert r.saturation == 70, f"fade-from-off at t=0.5: saturation should snap to 70, got {r.saturation}"
        assert r.brightness == 40, f"fade-from-off at t=0.5: brightness should be 40, got {r.brightness}"

    def test_mode_lerp_and_hue_shortest_path(self):
        """Cross-mode snaps to target color and lerps brightness.
        Same-mode lerps all fields. Hue takes the shortest arc on the wheel.
        """
        # cross-mode CT → HSB: color snaps, brightness averages
        ct  = LightProfile(mode="ct",  color_temp=3000, brightness=80)
        hsb = LightProfile(mode="hsb", hue=30, saturation=50, brightness=60)
        r = interpolate_profiles(ct, hsb, 0.5)
        assert r.mode == "hsb",       f"cross-mode ct→hsb: mode should snap to hsb, got {r.mode!r}"
        assert r.hue == 30,           f"cross-mode ct→hsb: hue should snap to 30, got {r.hue}"
        assert r.saturation == 50,    f"cross-mode ct→hsb: saturation should snap to 50, got {r.saturation}"
        assert r.brightness == 70,    f"cross-mode ct→hsb: brightness should lerp to 70, got {r.brightness}"

        # same-mode CT → CT: both fields lerp
        a = LightProfile(mode="ct", color_temp=2000, brightness=40)
        b = LightProfile(mode="ct", color_temp=6000, brightness=100)
        r = interpolate_profiles(a, b, 0.5)
        assert r.mode == "ct",          f"ct→ct: mode should stay ct, got {r.mode!r}"
        assert r.color_temp == 4000,    f"ct→ct at t=0.5: color_temp should be 4000, got {r.color_temp}"
        assert r.brightness == 70,      f"ct→ct at t=0.5: brightness should be 70, got {r.brightness}"

        # HSB brightness lerp
        a = LightProfile(mode="hsb", hue=0, saturation=0, brightness=0)
        b = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
        assert interpolate_profiles(a, b, 0.3).brightness == 30, \
            "hsb→hsb at t=0.3: brightness should be 30"

        # Hue shortest-path wrapping
        assert lerp_hue(350, 10,  0.5) == 0,  "350→10 at t=0.5: should wrap forward to 0"
        assert lerp_hue(20,  100, 0.5) == 60, "20→100 at t=0.5: no wrap needed, should be 60"
        assert lerp_hue(10,  350, 0.5) == 0,  "10→350 at t=0.5: should wrap backward to 0"


# ---------------------------------------------------------------------------
# Manual override detection
# ---------------------------------------------------------------------------

class TestDetectManualOverride:
    def test_all_override_cases(self):
        cases = [
            ({"on": True},  {},               "day",          "none",               "no last_applied → no override"),
            ({"on": True},  {"power": True},  "day",          "none",               "power unchanged → no override"),
            ({"on": False}, {"power": True},  "morning_ramp", "manual_off",         "user turned lamp OFF"),
            ({"on": True},  {"power": False}, "day",          "manual_on",          "user turned lamp ON during day"),
            ({"on": True},  {"power": False}, "pre_morning",  "manual_on",          "user turned lamp ON during pre_morning"),
            ({"on": True},  {"power": False}, "off",          "late_night_trigger", "user turned lamp ON after hard cutoff"),
        ]
        for light_state, last_applied, phase, expected, label in cases:
            result = detect_manual_override(light_state, last_applied, phase)
            assert result == expected, f"{label}: expected {expected!r}, got {result!r}"

    def test_stale_last_applied_suppresses_override(self):
        """If last_applied is >30 min old, a power mismatch is not treated as a user override.

        Regression: Pi offline overnight left last_applied["power"]=True from the previous
        evening. At morning_ramp start the lamp was off (user slept). detect_manual_override
        saw expected_on=True, actual_on=False and returned "manual_off", triggering DND
        until 07:00 and blocking the entire sunrise simulation.
        """
        now = dt(5, 16)
        stale_ts = (now - timedelta(hours=6)).isoformat()  # Pi was offline since 23:16
        last_applied = {"power": True, "phase": "hard_cutoff_ramp", "timestamp": stale_ts}
        result = detect_manual_override({"on": False}, last_applied, "morning_ramp", now=now)
        assert result == "none", "stale last_applied should not trigger manual_off"

    def test_fresh_last_applied_still_detects_override(self):
        """A recent last_applied (within 30 min) still triggers override detection."""
        now = dt(5, 16)
        fresh_ts = (now - timedelta(minutes=2)).isoformat()
        last_applied = {"power": True, "phase": "morning_ramp", "timestamp": fresh_ts}
        result = detect_manual_override({"on": False}, last_applied, "morning_ramp", now=now)
        assert result == "manual_off", "fresh last_applied should still detect manual_off"


# ---------------------------------------------------------------------------
# Stale sunrise guard
# ---------------------------------------------------------------------------

class TestGetMorningRampStart:
    def test_today_sunrise_used(self):
        """Sunrise on today's date is used as ramp start when earlier than morning_latest."""
        today_sunrise = dt(5, 14)  # 2024-06-15 05:14 UTC
        w = weather_mock(sunrise_hour=5, sunrise_min=14)
        result = get_morning_ramp_start(dt(0, 0), DEFAULT_CFG.morning_latest_start, w)
        assert result == today_sunrise

    def test_yesterday_sunrise_ignored(self):
        """Sunrise from a previous day (stale cache) falls back to morning_latest_start.

        Regression: OpenWeather cache from the previous evening carries yesterday's sunrise.
        After midnight the date rolls over; min(yesterday_sunrise, today_morning_latest)
        evaluates to yesterday_sunrise — a datetime in the past — causing morning_ramp_start
        to precede midnight and calculate_phase() to return morning_ramp at 00:00.
        """
        w = MagicMock()
        yesterday_sunrise = datetime(2024, 6, 14, 5, 13, tzinfo=UTC)  # previous day
        w.get_sunrise_dt.return_value = yesterday_sunrise
        now = dt(0, 0)  # midnight June 15
        result = get_morning_ramp_start(now, DEFAULT_CFG.morning_latest_start, w)
        assert result == datetime(2024, 6, 15, 6, 0, tzinfo=UTC), \
            "stale sunrise should fall back to morning_latest_start (06:00 today)"

    def test_no_weather_uses_morning_latest(self):
        result = get_morning_ramp_start(dt(0, 0), DEFAULT_CFG.morning_latest_start, None)
        assert result == datetime(2024, 6, 15, 6, 0, tzinfo=UTC)

    def test_midnight_not_morning_ramp_with_stale_sunrise(self):
        """calculate_phase returns pre_morning at midnight when weather cache is stale.

        Regression: midnight cron tick sees stale yesterday sunrise → morning_ramp_start
        in the past → phase=morning_ramp → lamp turns on at midnight.
        """
        w = MagicMock()
        w.get_sunrise_dt.return_value = datetime(2024, 6, 14, 5, 13, tzinfo=UTC)
        w.get_adjusted_sunset.return_value = dt(21, 0)
        w.is_dark_outside.return_value = False
        result = calculate_phase(dt(0, 0), w, DEFAULT_CFG, _empty_state())
        assert result == "pre_morning", \
            f"expected pre_morning at midnight with stale sunrise, got {result!r}"


# ---------------------------------------------------------------------------
# DND management
# ---------------------------------------------------------------------------

class TestDND:
    def test_apply_scope_and_respect(self):
        """apply_dnd_flag sets the right scope per phase; should_respect_dnd checks expiry."""
        # morning_ramp → morning_ramp scope, clears at full_morning_time (07:00)
        state = empty_state()
        apply_dnd_flag(state, "morning_ramp", dt(6, 15), DEFAULT_CFG)
        assert state["dnd_scope"] == "morning_ramp", \
            f"morning_ramp should set scope=morning_ramp, got {state['dnd_scope']!r}"
        assert parse_iso(state["do_not_disturb_until"]).hour == 7, \
            "morning_ramp DND should expire at 07:00"

        # evening_ramp / night_ramp → overnight scope
        for phase in ("evening_ramp", "night_ramp"):
            state = empty_state()
            apply_dnd_flag(state, phase, dt(19, 30), DEFAULT_CFG)
            assert state["dnd_scope"] == "overnight", \
                f"{phase} should set scope=overnight, got {state['dnd_scope']!r}"
            assert parse_iso(state["do_not_disturb_until"]).hour == 7, \
                f"{phase} DND should expire at 07:00 next morning"

        # day phase must NOT set DND
        state = empty_state()
        apply_dnd_flag(state, "day", dt(14, 0), DEFAULT_CFG)
        assert state["dnd_scope"] is None, \
            f"day phase must not set dnd_scope, got {state['dnd_scope']!r}"

        # should_respect_dnd: active before expiry, inactive after
        state = empty_state()
        state["do_not_disturb_until"] = dt(8, 0).isoformat()
        assert should_respect_dnd(state, dt(7, 0)) is True, \
            "DND with expiry at 08:00 should be active at 07:00"
        state["do_not_disturb_until"] = dt(6, 0).isoformat()
        assert should_respect_dnd(state, dt(7, 0)) is False, \
            "DND with expiry at 06:00 should have expired by 07:00"

    def test_clear_dnd_if_expired(self):
        """morning_ramp scope clears at full_morning_time; overnight scope clears at sunrise."""
        # morning_ramp: should NOT clear before full_morning_time (07:00)
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "morning_ramp"
        clear_dnd_if_expired(state, dt(6, 30), DEFAULT_CFG, None)
        assert state["dnd_scope"] == "morning_ramp", \
            "morning_ramp DND should not clear at 06:30 (before 07:00)"

        # morning_ramp: SHOULD clear at/after full_morning_time
        clear_dnd_if_expired(state, dt(7, 1), DEFAULT_CFG, None)
        assert state["dnd_scope"] is None, \
            "morning_ramp DND should clear after full_morning_time (07:01)"
        assert state["do_not_disturb_until"] is None, \
            "do_not_disturb_until should be None after morning_ramp DND clears"

        # overnight: clears at sunrise (05:30)
        state = empty_state()
        state["do_not_disturb_until"] = dt(7, 0).isoformat()
        state["dnd_scope"] = "overnight"
        clear_dnd_if_expired(state, dt(5, 45), DEFAULT_CFG, weather_mock(sunrise_hour=5, sunrise_min=30))
        assert state["dnd_scope"] is None, \
            "overnight DND should clear at sunrise (05:30); checked at 05:45"


# ---------------------------------------------------------------------------
# DND full-cycle integration
# ---------------------------------------------------------------------------

class TestDNDCycle:
    def test_manual_off_sets_dnd_then_next_tick_keeps_lamp_off(self):
        """Tick 1: user turns lamp off during morning_ramp → DND set.
        Tick 2: DND still active → lamp stays off despite active morning_ramp phase.
        """
        state_in = {**empty_state(), "last_applied": {"power": True, "phase": "morning_ramp"}}
        state1 = _fake_run(state_in, dt(6, 15), lamp_on=False)

        assert state1["dnd_scope"] == "morning_ramp", \
            "tick 1: manual_off during morning_ramp must set dnd_scope=morning_ramp"
        assert state1["do_not_disturb_until"] is not None, \
            "tick 1: DND expiry must be written to state"
        assert state1["last_applied"]["power"] is False, \
            "tick 1: lamp must remain off after manual_off"

        state2 = _fake_run(state1, dt(6, 20), lamp_on=False)
        assert state2["dnd_scope"] == "morning_ramp", \
            "tick 2: DND should not clear before full_morning_time (07:00)"
        assert state2["last_applied"]["power"] is False, \
            "tick 2: lamp must stay off while DND is active"


# ---------------------------------------------------------------------------
# Oscillation lockout
# ---------------------------------------------------------------------------

class TestOscillationLockout:
    def test_lockout_returns_cached_power_then_re_evaluates_after_expiry(self):
        """Within the lockout window, last known power is returned.
        After expiry (or no recorded toggle), weather is re-evaluated.
        """
        # Within lockout: each cached power value is returned directly
        for cached_power in (True, False):
            state = empty_state()
            state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=10)).isoformat()
            state["last_applied"] = {"power": cached_power}
            result = evaluate_day_darkness(None, state, dt(14, 0), DEFAULT_CFG)
            assert result is cached_power, \
                f"within lockout: cached_power={cached_power} should be returned as-is, got {result}"

        # After lockout expires (45 min ago) → weather re-evaluated
        for minutes_ago, label in ((45, "lockout expired"), (0, "no toggle recorded")):
            state = empty_state()
            if minutes_ago:
                state["last_daytime_toggle_at"] = (dt(14, 0) - timedelta(minutes=minutes_ago)).isoformat()
                state["last_applied"] = {"power": True}
            w = MagicMock()
            w.is_dark_outside.return_value = True
            evaluate_day_darkness(w, state, dt(14, 0), DEFAULT_CFG)
            assert w.is_dark_outside.call_count == 1, \
                f"{label}: weather.is_dark_outside should be called exactly once"
            w.reset_mock()


# ---------------------------------------------------------------------------
# Late-night override profile
# ---------------------------------------------------------------------------

class TestLateNightOverride:
    def test_profile_fades_and_morning_ramp_takes_priority(self):
        """Profile starts at LATE_NIGHT_PROFILE brightness and dims to 0 by the end.
        morning_ramp phase takes priority over an active late_night_override.
        """
        started = dt(23, 5)
        until   = started + timedelta(minutes=120)
        state   = {**empty_state(), "late_night_override": {"started_at": started.isoformat(), "until": until.isoformat()}}

        p_start = calculate_target_profile("late_night_override", started, None, DEFAULT_CFG, state)
        assert p_start.mode == LATE_NIGHT_PROFILE.mode, \
            f"start: mode should be {LATE_NIGHT_PROFILE.mode!r}, got {p_start.mode!r}"
        assert p_start.brightness == LATE_NIGHT_PROFILE.brightness, \
            f"start: brightness should be {LATE_NIGHT_PROFILE.brightness}, got {p_start.brightness}"

        p_mid = calculate_target_profile("late_night_override", started + timedelta(minutes=60), None, DEFAULT_CFG, state)
        assert p_mid.brightness < LATE_NIGHT_PROFILE.brightness, \
            f"midpoint: brightness {p_mid.brightness} should be less than start {LATE_NIGHT_PROFILE.brightness}"
        assert p_mid.brightness > 0, \
            f"midpoint: brightness {p_mid.brightness} should not be 0 yet"

        p_end = calculate_target_profile("late_night_override", until, None, DEFAULT_CFG, state)
        assert p_end.brightness == 0, \
            f"end (t=1.0): brightness should be 0 (fully off), got {p_end.brightness}"

        # morning_ramp beats late_night_override
        phase = calculate_phase(dt(6, 15), None, DEFAULT_CFG, state)
        assert phase == "morning_ramp", \
            f"morning_ramp should override active late_night_override, got {phase!r}"

    def test_late_night_override_cleared_by_controller_at_morning_ramp(self):
        """_run() clears late_night_override from state when morning_ramp starts."""
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
            patch("sunrise_sunset_controller.NanoleafLight", return_value=fake_light),
        ):
            _run(dt(6, 15))

        assert state_store["state"]["late_night_override"] is None, \
            "late_night_override should be cleared from state when morning_ramp starts"


# ---------------------------------------------------------------------------
# Party mode override handling
# ---------------------------------------------------------------------------

class TestPartyModeOverride:
    def test_manual_off_clears_party_and_morning_ramp_takes_priority(self):
        """manual_off during party clears party without setting DND.
        morning_ramp at tick start clears party and records that phase.
        """
        party_state = empty_state()
        party_state["party_mode"] = {
            "active": True,
            "ends_at": dt(23, 30).isoformat(),
            "fade_minutes": 30,
            "profile": {"mode": "hsb", "hue": 280, "saturation": 90, "brightness": 100},
        }
        party_state["last_applied"] = {"power": True, "phase": "party_mode"}
        result = _fake_run(party_state, dt(22, 0), lamp_on=False)

        assert result["party_mode"]["active"] is False, \
            "manual_off during party_mode must clear party_mode.active"
        assert result["do_not_disturb_until"] is None, \
            "manual_off during party must NOT set DND (party has its own exit logic)"

        # morning_ramp clears party and records correct phase
        ramp_state = empty_state()
        ramp_state["party_mode"] = {
            "active": True,
            "ends_at": dt(8, 0).isoformat(),
            "fade_minutes": 0,
            "profile": {"mode": "hsb", "hue": 280, "saturation": 90, "brightness": 100},
        }
        result = _fake_run(ramp_state, dt(6, 15), lamp_on=True)

        assert result["party_mode"]["active"] is False, \
            "morning_ramp start must clear party_mode.active"
        assert result["last_applied"]["phase"] == "morning_ramp", \
            f"last_applied.phase should be 'morning_ramp', got {result['last_applied']['phase']!r}"


# ---------------------------------------------------------------------------
# last_applied schema
# ---------------------------------------------------------------------------

class TestLastAppliedSchema:
    def test_last_applied_keys_and_timestamp_field_name(self):
        """last_applied must use the key 'timestamp' (not 'at') and contain phase/power/profile."""
        result = _fake_run(empty_state(), dt(14, 0), lamp_on=False)
        la = result["last_applied"]
        assert "timestamp" in la, \
            f"last_applied must have a 'timestamp' key; got keys: {list(la)}"
        assert "at" not in la, \
            "last_applied must NOT use the legacy 'at' key; use 'timestamp' instead"
        for key in ("phase", "power", "profile"):
            assert key in la, f"last_applied is missing required key {key!r}; got: {list(la)}"


# ---------------------------------------------------------------------------
# Anchor-time window — cron-interval agnostic
# ---------------------------------------------------------------------------

class TestIsAnchorTime:
    """_is_anchor_time fires exactly once per anchor window for any cron interval."""

    from weather.weather_cache import _is_anchor_time as _fn

    def _now(self, anchor_hour: int, anchor_min: int, offset_min: int) -> datetime:
        total = anchor_hour * 60 + anchor_min + offset_min
        return datetime(2024, 6, 15, total // 60, total % 60, tzinfo=UTC)

    # weather_fetch_evening defaults to 14:00; use it as the test anchor so we
    # don't need custom config for the basic window tests.

    @pytest.mark.parametrize("interval,offset,expected,label", [
        # interval=1: window [anchor, anchor+1) — only the exact minute fires
        (1,  0, True,  "interval=1: at anchor"),
        (1,  1, False, "interval=1: 1 min after anchor is outside"),
        # interval=2 (deployed default): window [anchor, anchor+2)
        (2,  0, True,  "interval=2: at anchor"),
        (2,  1, True,  "interval=2: 1 min after is inside window"),
        (2,  2, False, "interval=2: 2 min after is outside"),
        (2,  3, False, "interval=2: 3 min after is outside"),
        # interval=5 (original intent): window [anchor, anchor+5)
        (5,  0, True,  "interval=5: at anchor"),
        (5,  4, True,  "interval=5: 4 min after is last minute inside"),
        (5,  5, False, "interval=5: 5 min after is outside"),
        # interval=10
        (10, 0, True,  "interval=10: at anchor"),
        (10, 9, True,  "interval=10: 9 min after is inside"),
        (10, 10, False, "interval=10: 10 min after is outside"),
        # 1 min before the anchor never fires regardless of interval
        (1,  -1, False, "1 min before anchor (interval=1)"),
        (2,  -1, False, "1 min before anchor (interval=2)"),
        (5,  -1, False, "1 min before anchor (interval=5)"),
    ])
    def test_anchor_window(self, interval, offset, expected, label):
        from weather.weather_cache import _is_anchor_time
        now = self._now(14, 0, offset)
        c = cfg(cron_interval_minutes=interval)
        assert _is_anchor_time(now, c) is expected, label

    def test_non_anchor_time_never_fires(self):
        """A minute not near any anchor does not trigger for any reasonable interval."""
        from weather.weather_cache import _is_anchor_time
        # 12:30 is >30 min from nearest anchor (9:00 or 14:00)
        now = datetime(2024, 6, 15, 12, 30, tzinfo=UTC)
        for interval in (1, 2, 5, 10):
            c = cfg(cron_interval_minutes=interval)
            assert _is_anchor_time(now, c) is False, \
                f"12:30 should not fire for interval={interval}"

    def test_non_aligned_anchor_with_interval_2(self):
        """Anchor at HH:01 with interval=2: window [anchor, anchor+2) catches HH:01 and HH:02."""
        from weather.weather_cache import _is_anchor_time
        c = cfg(cron_interval_minutes=2, weather_fetch_evening=time(14, 1))
        # at 14:01 — inside window
        assert _is_anchor_time(datetime(2024, 6, 15, 14, 1, tzinfo=UTC), c) is True
        # at 14:02 — still inside [841, 843)
        assert _is_anchor_time(datetime(2024, 6, 15, 14, 2, tzinfo=UTC), c) is True
        # at 14:03 — outside
        assert _is_anchor_time(datetime(2024, 6, 15, 14, 3, tzinfo=UTC), c) is False
        # at 14:00 — before anchor, outside
        assert _is_anchor_time(datetime(2024, 6, 15, 14, 0, tzinfo=UTC), c) is False

    def test_multiple_anchors_any_fires(self):
        """A tick near any of the 5 anchors returns True."""
        from weather.weather_cache import _is_anchor_time
        c = cfg(cron_interval_minutes=2)
        # Default anchors: 0:00, 3:00, 9:00, 14:00, 20:00
        for hour in (0, 3, 9, 14, 20):
            now = datetime(2024, 6, 15, hour, 0, tzinfo=UTC)
            assert _is_anchor_time(now, c) is True, \
                f"tick at {hour:02d}:00 should fire (anchor at that hour)"

    def test_midnight_anchor_wraparound(self):
        """Anchor at 23:59 with interval=2 fires at 23:59 but not at 00:00."""
        from weather.weather_cache import _is_anchor_time
        c = cfg(
            cron_interval_minutes=2,
            weather_fetch_night=time(23, 59),
            weather_fetch_morning=time(1, 0),
            weather_fetch_midday=time(9, 0),
            weather_fetch_evening=time(14, 0),
            weather_fetch_late_evening=time(20, 0),
        )
        at_anchor = datetime(2024, 6, 15, 23, 59, tzinfo=UTC)
        at_midnight = datetime(2024, 6, 16, 0, 0, tzinfo=UTC)
        assert _is_anchor_time(at_anchor, c) is True
        assert _is_anchor_time(at_midnight, c) is False


# ---------------------------------------------------------------------------
# Weather backoff
# ---------------------------------------------------------------------------

class TestWeatherBackoff:
    def test_failure_increments_counter_and_schedules_retry(self):
        """A failed weather fetch increments consecutive_failures and sets next_retry_at."""
        from weather.weather_cache import get_weather
        state = empty_state()
        env = {"OPENWEATHER_LATITUDE": "47.6", "OPENWEATHER_LONGITUDE": "-122.1", "OPENWEATHER_AUTH_TOKEN": "tok"}
        with (
            patch("weather.weather_cache.OpenWeatherLight", side_effect=Exception("API down")),
            patch.dict("os.environ", env),
        ):
            get_weather(state, dt(10, 0), DEFAULT_CFG)
        f = state["weather_failure_state"]
        assert f["consecutive_failures"] == 1, \
            f"one fetch failure should set consecutive_failures=1, got {f['consecutive_failures']}"
        assert f["next_retry_at"] is not None, \
            "a failed fetch must schedule next_retry_at"

    def test_refresh_decisions(self):
        """should_refresh_weather: backoff blocks; absent/stale cache triggers; fresh skips;
        anchor time always forces a refresh even during backoff.
        """
        from weather.weather_cache import should_refresh_weather

        # In backoff at a non-anchor time → do not refresh
        state = empty_state()
        state["weather_failure_state"]["consecutive_failures"] = 2
        state["weather_failure_state"]["next_retry_at"] = (dt(14, 0) + timedelta(minutes=20)).isoformat()
        assert should_refresh_weather(state, dt(14, 5), DEFAULT_CFG) is False, \
            "in backoff at a non-anchor time should NOT refresh"

        # No cache, no backoff → refresh
        assert should_refresh_weather(empty_state(), dt(10, 0), DEFAULT_CFG) is True, \
            "no cache and no backoff should trigger refresh"

        # Fresh cache (fetched 1 h ago, max_age=5 h) → skip
        fresh = empty_state()
        fresh["weather_cache"] = {"fetched_at": dt(10, 0).isoformat(), "raw_data": {}}
        assert should_refresh_weather(fresh, dt(11, 0), DEFAULT_CFG) is False, \
            "fresh cache should suppress refresh"

        # Stale cache (fetched 2 h ago, max_age=1 h) → refresh
        stale = empty_state()
        stale["weather_cache"] = {"fetched_at": dt(10, 0).isoformat(), "raw_data": {}}
        assert should_refresh_weather(stale, dt(12, 0), cfg(weather_cache_max_age_hours=1)) is True, \
            "stale cache should trigger refresh"

        # Anchor time (14:00) overrides backoff → force refresh
        state = empty_state()
        state["weather_failure_state"]["consecutive_failures"] = 3
        state["weather_failure_state"]["next_retry_at"] = (dt(14, 0) + timedelta(hours=1)).isoformat()
        assert should_refresh_weather(state, dt(14, 0), DEFAULT_CFG) is True, \
            "anchor time (14:00) must force refresh even when in backoff"


# ---------------------------------------------------------------------------
# Lamp backoff
# ---------------------------------------------------------------------------

class TestLampBackoff:
    def test_backoff_lifecycle(self):
        """Detection, failure recording, and success reset all work as a state machine."""
        state = empty_state()

        # Detection: future retry → in backoff; past retry → not; None → not
        state["lamp_failure_state"]["next_retry_at"] = dt(14, 30).isoformat()
        assert is_lamp_in_backoff(state, dt(14, 0)) is True, \
            "retry scheduled in the future should put lamp in backoff"
        state["lamp_failure_state"]["next_retry_at"] = dt(13, 0).isoformat()
        assert is_lamp_in_backoff(state, dt(14, 0)) is False, \
            "retry time already past should NOT be in backoff"
        state["lamp_failure_state"]["next_retry_at"] = None
        assert is_lamp_in_backoff(state, dt(14, 0)) is False, \
            "no retry scheduled should NOT be in backoff"

        # First failure: increments counter, records exception type, schedules 5-min retry
        state = empty_state()
        handle_lamp_failure(state, dt(14, 0), DEFAULT_CFG, ConnectionError("unreachable"))
        f = state["lamp_failure_state"]
        assert f["consecutive_failures"] == 1, \
            f"first failure should set consecutive_failures=1, got {f['consecutive_failures']}"
        assert f["last_failure_type"] == "ConnectionError", \
            f"exception type should be recorded as 'ConnectionError', got {f['last_failure_type']!r}"
        retry_secs = (parse_iso(f["next_retry_at"]) - dt(14, 0)).total_seconds()
        assert retry_secs == 5 * 60, \
            f"first failure should schedule a 5-min retry, got {retry_secs / 60:.1f} min"

        # Success: resets counter and clears retry
        state["lamp_failure_state"]["consecutive_failures"] = 3
        state["lamp_failure_state"]["next_retry_at"] = dt(14, 30).isoformat()
        handle_lamp_success(state)
        f = state["lamp_failure_state"]
        assert f["consecutive_failures"] == 0, \
            f"success should reset consecutive_failures to 0, got {f['consecutive_failures']}"
        assert f["next_retry_at"] is None, \
            "success should clear next_retry_at"


# ---------------------------------------------------------------------------
# describe_color
# ---------------------------------------------------------------------------

class TestDescribeColor:
    def test_describe_color_all_cases(self):
        """Off, CT, HSB, and saturation-modifier outputs are correctly described."""
        cases = [
            (LightProfile(mode="hsb", brightness=0),                           "off",            None,       "brightness=0 → 'off'"),
            (LightProfile(mode="ct",  color_temp=6000, brightness=100),        "daylight white",  "full",    "CT 6000K full brightness"),
            (LightProfile(mode="ct",  color_temp=2500, brightness=20),         "warm white",      "dim",     "CT 2500K dim"),
            (NIGHT_PROFILE,                                                     "red",             "dim",     "NIGHT profile"),
            (LightProfile(mode="hsb", hue=280, saturation=90, brightness=100), "purple",          "full",    "party purple"),
            (LightProfile(mode="hsb", hue=120, saturation=5,  brightness=50),  "near white",      None,      "low saturation → near white"),
            (DAYTIME_ON_PROFILE,                                                "orange",          "dim",     "DAYTIME_ON profile"),
        ]
        for profile, expected_color, expected_brightness, label in cases:
            result = describe_color(profile)
            if expected_color == "off":
                assert result == "off", \
                    f"{label}: expected result 'off', got {result!r}"
            else:
                # DAYTIME_ON is warm orange-red — accept amber or red
                if label == "DAYTIME_ON profile":
                    assert "amber" in result or "red" in result, \
                        f"{label}: expected 'amber' or 'red' in result, got {result!r}"
                else:
                    assert expected_color in result, \
                        f"{label}: expected {expected_color!r} in result, got {result!r}"
            if expected_brightness:
                assert expected_brightness in result, \
                    f"{label}: expected brightness word {expected_brightness!r} in result, got {result!r}"
