"""tests/test_sparkle.py

Tests for the retained sparkle scatter effect in nanoleaf/sparkle.py.

The live current-guard was removed; sparkle.py is kept as reusable (currently
dead) code, reachable via `nanoleaf-cli preview sparkle`. These cover its pure
functions (K-count, even-spacing, two-mode selection, animData shape, payload)
plus the still-used CLI range validators. A couple of general run() timestamp
checks ride on the shared MockLamp wiring.
"""

import argparse
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from controller.config import Config, LightProfile
from controller.state import _empty_state, load_state, save_state
from nanoleaf.sparkle import (
    build_sparkle_animdata,
    build_sparkle_effect,
    calculate_guard_setting,
    even_spaced,
    hsb_to_rgb,
    select_dim_panels,
)
from tests.conftest import MockLamp, PANELS_51

TZ = ZoneInfo("America/Los_Angeles")


# ---------------------------------------------------------------------------
# animData parse helper
# ---------------------------------------------------------------------------

def _parse_animdata(anim: str):
    """Return (num_panels, [(id, frames, R, G, B, W, T), ...]) from an animData string."""
    tok = anim.split()
    n = int(tok[0])
    panels, idx = [], 1
    for _ in range(n):
        pid, frames = int(tok[idx]), int(tok[idx + 1])
        r, g, b, w, t = (int(tok[idx + 2 + j]) for j in range(5))
        panels.append((pid, frames, r, g, b, w, t))
        idx += 7
    return n, panels


# ---------------------------------------------------------------------------
# hsb_to_rgb
# ---------------------------------------------------------------------------

def test_hsb_to_rgb_known_values():
    assert hsb_to_rgb(0, 100, 100) == (255, 0, 0)
    assert hsb_to_rgb(120, 100, 100) == (0, 255, 0)
    assert hsb_to_rgb(0, 0, 100) == (255, 255, 255)
    assert hsb_to_rgb(0, 0, 0) == (0, 0, 0)


# ---------------------------------------------------------------------------
# calculate_guard_setting  -> (K, floor_brightness, ceiling_brightness)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("floor_pct", [70, 60, 50, 40, 30])
def test_sparkle_dims_up_to_cap_holds_ceiling(floor_pct):
    """White over budget: sparkle dims K (<= max_dim cap) panels; ceiling holds target."""
    white80 = LightProfile(mode="hsb", hue=0, saturation=0, brightness=80)
    k, floor_bri, ceiling = calculate_guard_setting(white80, floor_pct, 80, 51)
    assert 0 < k <= 10                        # capped at max_dim (default 10)
    assert ceiling == 80                      # ceiling always holds target
    assert floor_bri == int(80 * floor_pct / 100)


def test_k_zero_for_within_budget_color():
    """A colour whose flicker load is within budget → no panels dimmed."""
    amber = LightProfile(mode="hsb", hue=20, saturation=70, brightness=50)
    assert calculate_guard_setting(amber, 70, 80, 51) == (0, 50, 50)


def test_dim_count_capped_and_ceiling_held():
    """A colour far over budget dims at most max_dim panels; ceiling never lowered."""
    white = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
    k, floor_bri, ceiling = calculate_guard_setting(white, 70, 80, 51, max_dim=10)
    assert k == 10 and ceiling == 100         # brightness held; scatter capped
    k5, _, _ = calculate_guard_setting(white, 70, 80, 51, max_dim=5)
    assert k5 == 5                            # cap is configurable


def test_floor_pct_100_no_divzero():
    """floor_pct == 100 (floor == ceiling) must not divide by zero; still returns a K."""
    white = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
    k, floor_bri, ceiling = calculate_guard_setting(white, 100, 80, 51)
    assert 0 < k <= 10 and ceiling == 100


def test_guard_zero_panels():
    white = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
    assert calculate_guard_setting(white, 70, 80, 0) == (0, 100, 100)


def test_k_ceil_rounding():
    """K rounds up — even a sliver over budget dims a whole panel."""
    nearly = LightProfile(mode="hsb", hue=0, saturation=0, brightness=76)
    assert calculate_guard_setting(nearly, 70, 80, 51)[0] >= 1


@pytest.mark.parametrize("hue,sat,bri", [(0, 0, 80), (0, 0, 100), (40, 20, 100), (20, 70, 100)])
@pytest.mark.parametrize("floor_pct", [70, 75, 76, 90, 95, 100])
def test_guard_caps_dim_count_and_holds_ceiling(hue, sat, bri, floor_pct):
    """The guard never dims more than max_dim panels and never lowers the ceiling."""
    profile = LightProfile(mode="hsb", hue=hue, saturation=sat, brightness=bri)
    k, floor_bri, ceiling = calculate_guard_setting(profile, floor_pct, 80, 51)
    assert 0 <= k <= 10
    assert ceiling == bri                     # brightness/saturation held


# ---------------------------------------------------------------------------
# even_spaced
# ---------------------------------------------------------------------------

def test_even_spaced_count_and_determinism():
    a = even_spaced(PANELS_51, 11)
    b = even_spaced(PANELS_51, 11)
    assert a == b                      # deterministic, no RNG
    assert len(a) == 11
    assert set(a).issubset(PANELS_51)


def test_even_spaced_k_gt_len_no_crash():
    """k > len would make step 0 (slice error) — guarded; returns min(k,len)."""
    assert even_spaced([1, 2, 3], 5) == [1, 2, 3]


def test_even_spaced_zero():
    assert even_spaced(PANELS_51, 0) == []


# ---------------------------------------------------------------------------
# select_dim_panels (two-mode)
# ---------------------------------------------------------------------------

def _now():
    return datetime(2026, 6, 27, 12, 0, tzinfo=TZ)


def test_select_k_change_uses_even_spacing_no_random():
    """When K changes (empty/!=k stored), use deterministic even-spacing, no RNG."""
    state = {}
    cfg = Config()
    with patch("nanoleaf.sparkle.random.sample", side_effect=AssertionError("random used")):
        sel = select_dim_panels(state, PANELS_51, 11, _now(), cfg)
    assert sel == even_spaced(PANELS_51, 11)
    assert state["sparkle_dim_panels"] == sel


def test_select_rotation_reshuffles_after_interval():
    """K unchanged + rotation interval elapsed → random.sample, timestamp updated."""
    cfg = Config()                       # cron_interval_minutes=2, rotation_interval=10
    stored = even_spaced(PANELS_51, 11)
    old = (_now() - timedelta(minutes=60)).isoformat()   # 30 ticks ago >= 10
    state = {"sparkle_dim_panels": list(stored), "sparkle_last_rotation_at": old}
    fake = PANELS_51[:11]
    with patch("nanoleaf.sparkle.random.sample", return_value=list(fake)) as m:
        sel = select_dim_panels(state, PANELS_51, 11, _now(), cfg)
    m.assert_called_once()
    assert sel == fake
    assert state["sparkle_last_rotation_at"] == _now().isoformat()


def test_select_reuse_within_interval():
    """K unchanged + interval NOT elapsed → reuse stored, no RNG."""
    cfg = Config()
    stored = even_spaced(PANELS_51, 11)
    recent = (_now() - timedelta(minutes=2)).isoformat()   # 1 tick ago < 10
    state = {"sparkle_dim_panels": list(stored), "sparkle_last_rotation_at": recent}
    with patch("nanoleaf.sparkle.random.sample", side_effect=AssertionError("random used")):
        sel = select_dim_panels(state, PANELS_51, 11, _now(), cfg)
    assert sel == stored


def test_select_k_gt_population_no_crash():
    state = {}
    sel = select_dim_panels(state, [1, 2, 3], 5, _now(), Config())
    assert len(sel) == 3


# ---------------------------------------------------------------------------
# build_sparkle_animdata / build_sparkle_effect
# ---------------------------------------------------------------------------

def test_animdata_shape_and_split():
    dim_ids = even_spaced(PANELS_51, 11)
    # build now takes absolute floor/ceiling brightnesses (56 = 80 * 70%).
    anim = build_sparkle_animdata(PANELS_51, dim_ids, 20, 70, 56, 80, 30)
    n, panels = _parse_animdata(anim)

    assert n == 51
    assert len(panels) == 51
    ceil_rgb = hsb_to_rgb(20, 70, 80)
    floor_rgb = hsb_to_rgb(20, 70, 56)
    dim_count = 0
    for pid, frames, r, g, b, w, t in panels:
        assert frames == 1
        assert w == 0
        assert t == 30                     # transtime passed through
        if pid in set(dim_ids):
            assert (r, g, b) == floor_rgb
            dim_count += 1
        else:
            assert (r, g, b) == ceil_rgb
    assert dim_count == 11                  # exactly K panels at floor


def test_animdata_deterministic():
    dim = even_spaced(PANELS_51, 11)
    a = build_sparkle_animdata(PANELS_51, dim, 20, 70, 56, 80, 30)
    b = build_sparkle_animdata(PANELS_51, dim, 20, 70, 56, 80, 30)
    assert a == b


def test_build_sparkle_effect_payload():
    eff = build_sparkle_effect(PANELS_51, even_spaced(PANELS_51, 11), 20, 70, 56, 80, 30)
    assert eff["command"] == "display"
    assert eff["version"] == "2.0"
    assert eff["animType"] == "static"
    assert eff["loop"] is False
    assert eff["palette"] == []
    assert eff["animData"].split()[0] == "51"


# ---------------------------------------------------------------------------
# run() tick-timestamp behavior (general; uses the shared MockLamp wiring)
# ---------------------------------------------------------------------------

@pytest.fixture
def iso_state(tmp_path, monkeypatch):
    import controller.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "LOCK_PATH", tmp_path / "controller.lock")
    monkeypatch.setattr(state_mod, "PREVIEW_LOCK_PATH", tmp_path / "preview.lock")
    return tmp_path


def _wire(monkeypatch, lamp, config=None):
    import sunrise_sunset_controller as ctrl
    if config is None:
        config = Config()
    monkeypatch.setattr(ctrl, "NanoleafLight", lambda *_: lamp)
    monkeypatch.setattr(ctrl, "get_weather", lambda *_: None)
    monkeypatch.setattr(ctrl, "load_config", lambda: config)
    monkeypatch.setenv("NANOLEAF_IP_ADDRESS", "mock")
    monkeypatch.setenv("NANOLEAF_AUTH_TOKEN", "mock")
    return ctrl


def _seed_party(hue=0, sat=0, brightness=90, mode="hsb", color_temp=0):
    now = _now()
    st = _empty_state()
    st["party_mode"] = {
        "active": True,
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
        "fade_minutes": 0,
        "profile": {"mode": mode, "hue": hue, "saturation": sat,
                    "brightness": brightness, "color_temp": color_temp},
    }
    save_state(st)
    return now


def test_controller_last_tick_at_written(iso_state, monkeypatch):
    lamp = MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=20, sat=70, brightness=40)
    ctrl.run(now=now)
    assert load_state()["controller_last_tick_at"] == now.isoformat()


def test_controller_last_tick_at_written_during_backoff(iso_state, monkeypatch):
    # Even when the lamp is in backoff (early return), the tick timestamp is set.
    lamp = MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=20, sat=70, brightness=40)
    st = load_state()
    st["lamp_failure_state"] = {
        "consecutive_failures": 2,
        "last_failure_at": now.isoformat(),
        "last_failure_type": "NanoleafConnectionError",
        "next_retry_at": (now + timedelta(minutes=30)).isoformat(),
    }
    save_state(st)
    ctrl.run(now=now)
    assert load_state()["controller_last_tick_at"] == now.isoformat()
    assert "set_hsb" not in lamp.names()   # backoff → no lamp write


# ---------------------------------------------------------------------------
# CLI — sparkle range validators (still used by `preview sparkle`)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "validator_name, valid, invalid",
    [
        ("validate_sparkle_floor",     ["0", "100"],      ["101", "-1"]),  # 0-100
        ("validate_sparkle_transtime", ["0", "30", "200"], ["201", "-1"]),  # 0-200
    ],
)
def test_cli_range_validators(validator_name, valid, invalid):
    import nanoleaf_cli._validation as v
    validator = getattr(v, validator_name)
    for s in valid:
        assert validator(s) == int(s)
    for s in invalid:
        with pytest.raises(argparse.ArgumentTypeError):
            validator(s)
