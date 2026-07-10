"""tests/test_sparkle.py

Tests for the static (animType:"static") sparkle current guard:
  - pure functions in nanoleaf/sparkle.py (K-count, even-spacing, two-mode
    selection, animData shape, payload)
  - the re-wired controller guard, driven through the real run()
  - manual-recolor (P1-7) override detection
  - CLI validators / party --floor override
"""

import argparse
import json
import types
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from controller.config import Config, LightProfile
from controller.state import _empty_state, detect_manual_override, load_state, save_state
from nanoleaf.sparkle import (
    build_sparkle_animdata,
    build_sparkle_effect,
    calculate_guard_setting,
    even_spaced,
    flicker_load,
    hsb_to_rgb,
    power_fraction,
    select_dim_panels,
)

TZ = ZoneInfo("America/Los_Angeles")
PANELS_51 = [i for i in range(1, 53) if i != 6]   # ids 1-52 minus Rhythm (id 6)


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
# hsb_to_rgb / power_fraction
# ---------------------------------------------------------------------------

def test_hsb_to_rgb_known_values():
    assert hsb_to_rgb(0, 100, 100) == (255, 0, 0)
    assert hsb_to_rgb(120, 100, 100) == (0, 255, 0)
    assert hsb_to_rgb(0, 0, 100) == (255, 255, 255)
    assert hsb_to_rgb(0, 0, 0) == (0, 0, 0)


def test_power_fraction():
    assert power_fraction((255, 255, 255)) == pytest.approx(1.0)
    assert power_fraction((0, 0, 0)) == 0.0
    # Warm amber draws far less than white at the same brightness.
    amber = hsb_to_rgb(20, 70, 90)
    assert power_fraction(amber) < power_fraction(hsb_to_rgb(0, 0, 90))


# ---------------------------------------------------------------------------
# calculate_guard_setting  -> (K, floor_brightness, ceiling_brightness)
# ---------------------------------------------------------------------------

def _safe_total(threshold, n):
    return n * (threshold - 5) / 100.0


def _rendered_total(hue, sat, k, floor_bri, ceiling_bri, n):
    """Actual aggregate flicker load: (N-K) ceiling panels + K floor panels."""
    lc = flicker_load(hsb_to_rgb(hue, sat, ceiling_bri))
    lf = flicker_load(hsb_to_rgb(hue, sat, floor_bri))
    return (n - k) * lc + k * lf


@pytest.mark.parametrize("floor_pct", [70, 60, 50, 40, 30])
def test_sparkle_keeps_ceiling_within_flicker_budget(floor_pct):
    """White over budget: sparkle dims K panels, ceiling stays at target, aggregate within budget."""
    white80 = LightProfile(mode="hsb", hue=0, saturation=0, brightness=80)
    k, floor_bri, ceiling = calculate_guard_setting(white80, floor_pct, 80, 51)
    assert 0 < k <= 51
    assert ceiling == 80                      # ceiling stays at target — sparkle, not cap
    assert _rendered_total(0, 0, k, floor_bri, ceiling, 51) <= _safe_total(80, 51) + 1e-9


def test_k_zero_for_within_budget_color():
    """A colour whose flicker load is within budget → no panels dimmed."""
    amber = LightProfile(mode="hsb", hue=20, saturation=70, brightness=50)
    assert calculate_guard_setting(amber, 70, 80, 51) == (0, 50, 50)


def test_high_floor_lowers_floor_at_runtime_keeps_ceiling():
    """floor_pct too high to meet budget → lower the FLOOR at runtime, ceiling stays at target."""
    white = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
    k, floor_bri, ceiling = calculate_guard_setting(white, 90, 80, 51)
    assert 0 < k <= 51
    assert ceiling == 100                     # ceiling NOT capped — floor was lowered instead
    assert floor_bri < 90                     # runtime floor dropped well below the configured 90%
    assert _rendered_total(0, 0, k, floor_bri, ceiling, 51) <= _safe_total(80, 51) + 1e-9


def test_floor_pct_100_lowers_floor_no_divzero():
    """floor_pct == 100 can't scatter at that floor → runtime-lower the floor (no div-by-zero)."""
    white = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
    k, floor_bri, ceiling = calculate_guard_setting(white, 100, 80, 51)
    assert k > 0 and ceiling == 100
    assert _rendered_total(0, 0, k, floor_bri, ceiling, 51) <= _safe_total(80, 51) + 1e-9


def test_guard_zero_panels():
    white = LightProfile(mode="hsb", hue=0, saturation=0, brightness=100)
    assert calculate_guard_setting(white, 70, 80, 0) == (0, 100, 100)


def test_k_ceil_rounding():
    """K rounds up — even a sliver over budget dims a whole panel."""
    nearly = LightProfile(mode="hsb", hue=0, saturation=0, brightness=76)
    assert calculate_guard_setting(nearly, 70, 80, 51)[0] >= 1


@pytest.mark.parametrize("hue,sat,bri", [(0, 0, 80), (0, 0, 100), (40, 20, 100), (20, 70, 100)])
@pytest.mark.parametrize("floor_pct", [70, 75, 76, 90, 95, 100])
def test_budget_invariant_always_met(hue, sat, bri, floor_pct):
    """Whenever the guard returns a setting, total rendered draw must be <= safe_total."""
    profile = LightProfile(mode="hsb", hue=hue, saturation=sat, brightness=bri)
    k, floor_bri, ceiling = calculate_guard_setting(profile, floor_pct, 80, 51)
    assert _rendered_total(hue, sat, k, floor_bri, ceiling, 51) <= _safe_total(80, 51) + 1e-9


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
# Controller integration — driven through the real run()
# ---------------------------------------------------------------------------

class _MockLamp:
    DEFAULT_PANEL_IDS = list(PANELS_51)

    def __init__(self, panel_ids=None, panel_ids_raises=False, write_ok=True,
                 write_raises=None):
        self._state = {"on": False, "hue": 0, "sat": 0, "brightness": 0,
                       "ct": 4000, "colorMode": "hs"}
        self.calls = []
        self._panel_ids = list(self.DEFAULT_PANEL_IDS if panel_ids is None else panel_ids)
        self._panel_ids_raises = panel_ids_raises
        self._write_ok = write_ok
        self._write_raises = write_raises

    def get_full_state(self, retries=2, retry_delay=10.0, with_panels=False):
        s = dict(self._state)
        if with_panels:
            # Mirrors the real lamp: a missing/unreachable layout yields [].
            s["panel_ids"] = [] if self._panel_ids_raises else list(self._panel_ids)
        return s

    def get_panel_ids(self):
        self.calls.append(("get_panel_ids",))
        if self._panel_ids_raises:
            raise RuntimeError("layout unreachable")
        return list(self._panel_ids)

    def write_effect(self, effect):
        self.calls.append(("write_effect", effect))
        if self._write_raises is not None:
            raise self._write_raises
        if self._write_ok:
            self._state.update({"colorMode": "effect", "hue": 0, "sat": 0,
                                "ct": 1200, "brightness": 20})
        return self._write_ok

    def set_hsb(self, hue, sat, bri, on=None):
        self.calls.append(("set_hsb", hue, sat, bri, on))
        self._state.update({"hue": hue, "sat": sat, "brightness": bri, "colorMode": "hs"})
        if on is not None:
            self._state["on"] = on
        return True

    def set_color_temp_and_brightness(self, ct, bri, on=None):
        self.calls.append(("set_ct", ct, bri, on))
        self._state.update({"ct": ct, "brightness": bri, "colorMode": "ct"})
        if on is not None:
            self._state["on"] = on
        return True

    def power_on(self):
        self.calls.append(("power_on",))
        self._state["on"] = True
        return True

    def power_off(self):
        self.calls.append(("power_off",))
        self._state["on"] = False
        return True

    def names(self):
        return [c[0] for c in self.calls]


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


def _seed_party(hue=0, sat=0, brightness=90, mode="hsb", color_temp=0, floor=None):
    now = _now()
    st = _empty_state()
    pm = {
        "active": True,
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(),
        "fade_minutes": 0,
        "profile": {"mode": mode, "hue": hue, "saturation": sat,
                    "brightness": brightness, "color_temp": color_temp},
    }
    if floor is not None:
        pm["sparkle_override"] = {"floor_pct": floor}
    st["party_mode"] = pm
    save_state(st)
    return now


def test_hsb_above_threshold_writes_effect(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)   # white → K>0
    ctrl.run(now=now)
    assert "write_effect" in lamp.names()
    assert "set_hsb" not in lamp.names()
    st = load_state()
    assert st["last_applied"]["current_guard_active"] == "sparkle"
    assert st["last_applied"]["effect_active"] is True


def test_hsb_within_budget_uses_set_hsb(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=20, sat=70, brightness=30)   # warm + dim → flicker load within budget
    ctrl.run(now=now)
    assert "set_hsb" in lamp.names()
    assert "write_effect" not in lamp.names()


def test_hsb_warm_k0_uses_set_hsb_no_cap(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=20, sat=70, brightness=30)   # warm + dim → within budget, K=0
    ctrl.run(now=now)
    assert "write_effect" not in lamp.names()
    set_calls = [c for c in lamp.calls if c[0] == "set_hsb"]
    assert set_calls and set_calls[0][3] == 30          # not capped
    assert load_state()["last_applied"].get("current_guard_active") is None


def test_ct_above_threshold_caps_no_effect(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(mode="ct", color_temp=6000, brightness=90)
    ctrl.run(now=now)
    assert "write_effect" not in lamp.names()
    ct_calls = [c for c in lamp.calls if c[0] == "set_ct"]
    expected_cap = Config().current_guard_threshold - 5   # derive from default threshold
    assert ct_calls and ct_calls[0][2] == expected_cap
    assert load_state()["last_applied"]["current_guard_active"] == "brightness_cap"


def test_guard_disabled_no_effect(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp, config=Config(current_guard_enabled=False))
    now = _seed_party(hue=0, sat=0, brightness=100)
    ctrl.run(now=now)
    assert "write_effect" not in lamp.names()
    assert "set_hsb" in lamp.names()


def test_sparkle_path_makes_no_separate_get_panel_ids_call(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)
    ctrl.run(now=now + timedelta(minutes=2))
    # Panels come from get_full_state(with_panels=True) — the run() path must make
    # ZERO separate get_panel_ids device GETs (it fires on every high-consumption
    # tick, so a second GET per tick would be unacceptable).
    assert lamp.names().count("get_panel_ids") == 0
    assert load_state()["panel_ids"] == PANELS_51


def test_panel_set_change_updates_cache(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)
    assert load_state()["panel_ids"] == PANELS_51
    lamp._panel_ids = PANELS_51[:-1]                     # a tile removed
    ctrl.run(now=now + timedelta(minutes=2))
    st = load_state()
    assert st["panel_ids"] == PANELS_51[:-1]             # cache refreshed
    assert set(st["sparkle_dim_panels"]).issubset(set(PANELS_51[:-1]))   # no stale IDs


def test_no_panel_ids_falls_back_to_cap(iso_state, monkeypatch):
    lamp = _MockLamp(panel_ids_raises=True)
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)
    assert "write_effect" not in lamp.names()
    assert load_state()["last_applied"]["current_guard_active"] == "brightness_cap"


def test_no_panel_ids_does_not_raise_dim_color(iso_state, monkeypatch):
    # RISK-1: with no panel IDs we cannot sparkle, so cap DOWN only —
    # a dim colour (40 < threshold-5=45) must NOT be brightened up to the cap.
    lamp = _MockLamp(panel_ids_raises=True)
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=40)
    ctrl.run(now=now)
    st = load_state()
    assert "write_effect" not in lamp.names()
    assert st["last_applied"]["profile"]["brightness"] == 40           # not raised to 45
    assert st["last_applied"].get("current_guard_active") is None      # not labelled a cap


def test_write_effect_4xx_degrades_to_cap(iso_state, monkeypatch):
    # write_effect returns False = lamp rejected the payload (4xx) → degrade to a
    # capped solid colour, NO backoff.
    lamp = _MockLamp(write_ok=False)
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)
    st = load_state()
    assert st["lamp_failure_state"]["consecutive_failures"] == 0
    assert st["last_applied"]["current_guard_active"] == "brightness_cap"
    assert "set_hsb" in lamp.names()


def test_write_effect_connection_error_triggers_backoff(iso_state, monkeypatch):
    # A transient connection failure re-raises → handle_lamp_failure (backoff).
    from nanoleaf.nanoleafLight import NanoleafConnectionError
    lamp = _MockLamp(write_raises=NanoleafConnectionError("timeout"))
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)
    st = load_state()
    assert st["lamp_failure_state"]["consecutive_failures"] == 1
    assert st["lamp_failure_state"]["next_retry_at"] is not None


def test_floor_pct_100_lowers_floor_and_sparkles(iso_state, monkeypatch):
    # floor_pct=100 can't scatter at that floor → runtime-lower the floor and
    # sparkle (ceiling stays at target). NOT a flat cap.
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90, floor=100)
    ctrl.run(now=now)
    assert "write_effect" in lamp.names()
    assert load_state()["last_applied"]["current_guard_active"] == "sparkle"


def test_skip_guard_unchanged_effect(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)
    ctrl.run(now=now + timedelta(minutes=2))            # identical effect, lamp ON
    assert lamp.names().count("write_effect") == 1      # second tick skipped


def test_rewrite_forced_after_power_on(iso_state, monkeypatch):
    # RISK-2: if the lamp was OFF, power_on drops the volatile effect, so we must
    # rewrite even when the hash matches and NVRAM still reads colorMode "effect".
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    ctrl.run(now=now)                                   # write #1, lamp on, hash stored
    assert lamp.names().count("write_effect") == 1
    # Lamp turned off (volatile effect lost) without tripping manual_off:
    # expected power already False, NVRAM still reports "effect".
    st = load_state()
    st["last_applied"]["power"] = False
    save_state(st)
    lamp._state["on"] = False
    lamp._state["colorMode"] = "effect"
    ctrl.run(now=now + timedelta(minutes=2))
    assert lamp.names().count("write_effect") == 2      # forced rewrite, not skipped


def test_guard_skipped_when_should_be_on_false(iso_state, monkeypatch):
    # MINOR-3: a high-power colour that WOULD fire the guard, but the lamp is going
    # off (manual-off during party → should_be_on False) → no sparkle write.
    lamp = _MockLamp()
    lamp._state["on"] = False                           # user turned it off
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    st = load_state()
    st["last_applied"] = {"power": True, "profile": {}, "phase": "party_mode",
                          "timestamp": now.isoformat()}
    save_state(st)
    ctrl.run(now=now)
    assert "write_effect" not in lamp.names()
    assert load_state()["last_applied"].get("current_guard_active") is None


def test_party_floor_override_reaches_effect(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=60, floor=30)
    ctrl.run(now=now)
    effect = next(c[1] for c in lamp.calls if c[0] == "write_effect")
    _, panels = _parse_animdata(effect["animData"])
    floor_rgb = hsb_to_rgb(0, 0, int(60 * 30 / 100))  # floor at 30% (impl uses int())
    assert any((r, g, b) == floor_rgb for _, _, r, g, b, _, _ in panels)


def test_controller_last_tick_at_written(iso_state, monkeypatch):
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=20, sat=70, brightness=40)
    ctrl.run(now=now)
    assert load_state()["controller_last_tick_at"] == now.isoformat()


def test_controller_last_tick_at_written_during_backoff(iso_state, monkeypatch):
    # Even when the lamp is in backoff (early return), the tick timestamp is set.
    lamp = _MockLamp()
    ctrl = _wire(monkeypatch, lamp)
    now = _seed_party(hue=0, sat=0, brightness=90)
    st = load_state()
    st["lamp_failure_state"] = {
        "consecutive_failures": 2,
        "last_failure_at": now.isoformat(),
        "last_failure_type": "NanoleafConnectionError",
        "next_retry_at": (now + timedelta(minutes=30)).isoformat(),
    }
    save_state(st)
    ctrl.run(now=now)
    st2 = load_state()
    assert st2["controller_last_tick_at"] == now.isoformat()
    assert "write_effect" not in lamp.names()   # backoff → no lamp write


# ---------------------------------------------------------------------------
# P1-7 manual-recolor override detection
# ---------------------------------------------------------------------------

def _last_applied(effect=True):
    return {"power": True, "effect_active": effect,
            "timestamp": _now().isoformat()}


def test_recolor_detected_when_colormode_leaves_effect():
    light_state = {"on": True, "colorMode": "hs"}
    assert detect_manual_override(light_state, _last_applied(), "party_mode", now=_now()) == "manual_recolor"


def test_no_recolor_while_effect_running():
    light_state = {"on": True, "colorMode": "effect"}
    assert detect_manual_override(light_state, _last_applied(), "party_mode", now=_now()) == "none"


def test_no_recolor_when_last_tick_was_not_effect():
    light_state = {"on": True, "colorMode": "hs"}
    assert detect_manual_override(light_state, _last_applied(effect=False), "party_mode", now=_now()) == "none"


def test_manual_off_during_effect():
    light_state = {"on": False, "colorMode": "effect"}
    assert detect_manual_override(light_state, _last_applied(), "night_ramp", now=_now()) == "manual_off"


def test_stale_last_applied_suppresses_recolor():
    stale = {"power": True, "effect_active": True,
             "timestamp": (_now() - timedelta(minutes=45)).isoformat()}
    light_state = {"on": True, "colorMode": "hs"}
    assert detect_manual_override(light_state, stale, "party_mode", now=_now()) == "none"


# ---------------------------------------------------------------------------
# CLI — party --floor and validators
# ---------------------------------------------------------------------------

def test_cli_party_floor_writes_override(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr("controller.state.STATE_PATH", state_path)
    monkeypatch.setattr("controller.state.STATE_DIR", tmp_path)

    from nanoleaf_cli.commands.party import _start
    args = types.SimpleNamespace(
        floor=30, hue=None, sat=None, brightness=None, color=None,
        until=None, fade=None, fade_duration=None,
    )
    with patch("nanoleaf_cli.commands.party.confirm_party"):
        _start(args)

    state = json.loads(state_path.read_text())
    assert state["party_mode"]["sparkle_override"] == {"floor_pct": 30}


def test_cli_floor_validation():
    from nanoleaf_cli._validation import validate_sparkle_floor
    assert validate_sparkle_floor("0") == 0
    assert validate_sparkle_floor("100") == 100
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_floor("101")
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_floor("-1")


def test_cli_transtime_validation():
    from nanoleaf_cli._validation import validate_sparkle_transtime
    assert validate_sparkle_transtime("0") == 0
    assert validate_sparkle_transtime("30") == 30
    assert validate_sparkle_transtime("200") == 200
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_transtime("201")
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_transtime("-1")
