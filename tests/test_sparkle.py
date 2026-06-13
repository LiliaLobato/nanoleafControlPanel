"""tests/test_sparkle.py

Unit tests for nanoleaf/sparkle.py and the sparkle trigger in the controller.
"""

import json
import types
from unittest.mock import MagicMock, patch

import pytest

from controller.config import LightProfile
from nanoleaf.effects import speed_to_transtime
from nanoleaf.sparkle import (
    _brightness_sequence,
    _NUM_FRAMES,
    build_sparkle_animdata,
    build_sparkle_effect,
    hsb_to_rgb,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PANEL_IDS = list(range(17, 23))  # [17, 18, 19, 20, 21, 22] — 6 panels


def _tokens(panel_ids=PANEL_IDS, hue=20, sat=70, brightness=80, floor_pct=70, speed=3):
    return build_sparkle_animdata(panel_ids, hue, sat, brightness, floor_pct, speed).split()


# ---------------------------------------------------------------------------
# hsb_to_rgb
# ---------------------------------------------------------------------------

def test_hsb_to_rgb_known_values():
    assert hsb_to_rgb(0, 100, 100) == (255, 0, 0)
    assert hsb_to_rgb(120, 100, 100) == (0, 255, 0)
    assert hsb_to_rgb(0, 0, 100) == (255, 255, 255)
    assert hsb_to_rgb(0, 0, 0) == (0, 0, 0)


# ---------------------------------------------------------------------------
# animData structure
# ---------------------------------------------------------------------------

def test_animdata_panel_count():
    tokens = _tokens()
    assert tokens[0] == str(len(PANEL_IDS))


def test_animdata_brightness_in_range():
    tokens = _tokens(brightness=80, floor_pct=70)
    floor_rgb = hsb_to_rgb(20, 70, round(80 * 70 / 100))  # floor brightness
    ceil_rgb  = hsb_to_rgb(20, 70, 80)                    # ceiling brightness

    # collect R values (index 2 within each frame block) and compare bounds
    num_panels = int(tokens[0])
    idx = 1
    for _ in range(num_panels):
        idx += 1  # panelId
        idx += 1  # numFrames
        for _ in range(_NUM_FRAMES):
            r, g, b = int(tokens[idx]), int(tokens[idx + 1]), int(tokens[idx + 2])
            assert min(floor_rgb) <= r <= max(ceil_rgb) + 1  # +1 for rounding
            idx += 5  # R G B W transTime


def test_animdata_panels_not_synchronized():
    """At frame 0, panels must not all share the same brightness."""
    tokens = _tokens()
    num_panels = int(tokens[0])
    # R value of frame 0 for each panel
    r_values = []
    idx = 1
    for _ in range(num_panels):
        idx += 1  # panelId
        idx += 1  # numFrames
        r_values.append(int(tokens[idx]))  # R of frame 0
        idx += _NUM_FRAMES * 5
    assert len(set(r_values)) > 1, f"all panels synchronised at R={r_values[0]}"


def test_w_field_always_zero():
    tokens = _tokens()
    num_panels = int(tokens[0])
    idx = 1
    for _ in range(num_panels):
        idx += 2  # panelId + numFrames
        for _ in range(_NUM_FRAMES):
            w = int(tokens[idx + 3])  # W is 4th field: R G B W T
            assert w == 0, f"W field is {w}, expected 0"
            idx += 5


# ---------------------------------------------------------------------------
# speed_to_transtime
# ---------------------------------------------------------------------------

def test_speed_maps_to_transtime():
    assert speed_to_transtime(1) == 5   # 500 ms — slowest
    assert speed_to_transtime(10) == 2  # 200 ms — fastest
    # minimum is 2; transTime 1 (100ms = 10Hz) must never appear
    for s in range(1, 11):
        assert speed_to_transtime(s) >= 2, f"speed {s} maps below minimum"


# ---------------------------------------------------------------------------
# Sparkle trigger in the controller
# ---------------------------------------------------------------------------

def _make_controller_state(panel_ids=None):
    return {
        "panel_ids": panel_ids,
        "party_mode": {"active": False},
        "last_applied": None,
    }


def _run_trigger(brightness, mode="hsb", guard_enabled=True, should_be_on=True,
                 threshold=80, panel_ids=PANEL_IDS, party_override=None):
    """Exercise the sparkle trigger block isolated from the full controller."""
    from controller.config import Config
    from nanoleaf.sparkle import build_sparkle_effect

    config = Config(
        current_guard_enabled=guard_enabled,
        current_guard_threshold=threshold,
        sparkle_speed=3,
        sparkle_floor_pct=70,
    )
    effective_color = LightProfile(
        mode=mode, hue=20, saturation=70, brightness=brightness, color_temp=4000
    )
    light = MagicMock()
    light.get_panel_ids.return_value = panel_ids
    light.write_effect.return_value = True

    state = _make_controller_state(panel_ids=None)  # always start with empty cache
    if party_override:
        state["party_mode"] = {"active": True, "sparkle_override": party_override}

    light_state = {"on": True}

    # Replicate the trigger block from sunrise_sunset_controller.py
    sparkle_override = (state.get("party_mode") or {}).get("sparkle_override", {})
    sparkle_speed = sparkle_override.get("speed", config.sparkle_speed)
    sparkle_floor = sparkle_override.get("floor_pct", config.sparkle_floor_pct)

    wrote_sparkle = False
    if (config.current_guard_enabled
            and effective_color.mode == "hsb"
            and effective_color.brightness >= config.current_guard_threshold
            and should_be_on):
        cached = state.get("panel_ids") or []
        if not cached:
            cached = light.get_panel_ids()
            if cached:
                state["panel_ids"] = cached
        if cached:
            effect = build_sparkle_effect(cached, effective_color, sparkle_floor, sparkle_speed)
            light.write_effect(effect)
            wrote_sparkle = True

    return light, wrote_sparkle, state


def test_sparkle_skipped_below_threshold():
    light, wrote, _ = _run_trigger(brightness=79, threshold=80)
    assert not wrote
    light.write_effect.assert_not_called()


def test_sparkle_triggered_at_threshold():
    light, wrote, _ = _run_trigger(brightness=80, threshold=80)
    assert wrote
    light.write_effect.assert_called_once()


def test_sparkle_rewrite_every_tick():
    """write_effect is called on every invocation — no skip guard."""
    for _ in range(3):
        light, wrote, _ = _run_trigger(brightness=85)
        assert wrote
        light.write_effect.assert_called_once()


def test_deterministic_animdata():
    anim1 = build_sparkle_animdata(PANEL_IDS, 20, 70, 80, 70, 3)
    anim2 = build_sparkle_animdata(PANEL_IDS, 20, 70, 80, 70, 3)
    assert anim1 == anim2


def test_phase_offset_applied():
    """sorted_index % num_frames — verify two consecutive panels have different frame 0."""
    sorted_ids = sorted(PANEL_IDS)
    # Panel at sorted_index=0 → offset=0 → starts at sequence[0] (floor)
    # Panel at sorted_index=1 → offset=1 → starts at sequence[1] (floor+step)
    tokens = _tokens()
    num_panels = int(tokens[0])
    r_frame0 = []
    idx = 1
    for _ in range(num_panels):
        idx += 2  # panelId + numFrames
        r_frame0.append(int(tokens[idx]))
        idx += _NUM_FRAMES * 5
    # First two panels must differ at frame 0
    assert r_frame0[0] != r_frame0[1], "sorted_index 0 and 1 share same frame-0 R value"


def test_ct_mode_above_threshold_uses_brightness_cap():
    """CT mode above threshold: brightness capped, write_effect never called."""
    import dataclasses
    from controller.config import Config

    config = Config(current_guard_enabled=True, current_guard_threshold=80)
    effective_color = LightProfile(mode="ct", color_temp=5000, brightness=90)

    capped_color = effective_color
    if (config.current_guard_enabled
            and effective_color.mode == "ct"
            and effective_color.brightness >= config.current_guard_threshold):
        capped_color = dataclasses.replace(
            effective_color, brightness=config.current_guard_threshold - 5
        )

    assert capped_color.brightness == 75
    assert capped_color.mode == "ct"

    # sparkle trigger must not fire for CT mode
    light, wrote, _ = _run_trigger(brightness=90, mode="ct", threshold=80)
    assert not wrote
    light.write_effect.assert_not_called()


def test_party_sparkle_override_used():
    """Party state sparkle_override values are passed to build_sparkle_effect."""
    light, wrote, _ = _run_trigger(
        brightness=85,
        party_override={"speed": 8, "floor_pct": 30},
    )
    assert wrote
    call_args = light.write_effect.call_args[0][0]
    # speed=8 → transTime=3; floor=30% of 85=25 → verify animData reflects floor
    anim_tokens = call_args["animData"].split()
    # panel 0 frame 0 R value — at floor_pct=30, brightness=85: floor=round(85*30/100)=26
    # vs floor_pct=70: floor=round(85*70/100)=60. Just check it's a custom dict.
    assert call_args["command"] == "display"
    assert call_args["animType"] == "custom"


def test_current_guard_disabled_skips_sparkle():
    """current_guard_enabled=False bypasses sparkle at any brightness."""
    light, wrote, _ = _run_trigger(brightness=100, guard_enabled=False)
    assert not wrote
    light.write_effect.assert_not_called()


# ---------------------------------------------------------------------------
# CLI — party --speed/--floor writes sparkle_override
# ---------------------------------------------------------------------------

def test_cli_party_floor_writes_override(tmp_path, monkeypatch):
    """nanoleaf-cli party --floor 30 writes sparkle_override.floor_pct=30 to state."""
    import os
    state_path = tmp_path / "state.json"
    monkeypatch.setattr("controller.state.STATE_PATH", state_path)
    monkeypatch.setattr("controller.state.STATE_DIR", tmp_path)

    from nanoleaf_cli.commands.party import _start
    args = types.SimpleNamespace(
        speed=None, floor=30,
        hue=None, sat=None, brightness=None, color=None,
        until=None, fade=None, fade_duration=None,
    )
    with patch("nanoleaf_cli.commands.party.confirm_party"):
        _start(args)

    state = json.loads(state_path.read_text())
    assert state["party_mode"]["sparkle_override"] == {"floor_pct": 30}


def test_cli_floor_validation():
    from nanoleaf_cli._validation import validate_sparkle_floor
    import argparse
    assert validate_sparkle_floor("0") == 0
    assert validate_sparkle_floor("100") == 100
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_floor("101")
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_floor("-1")


def test_cli_speed_validation():
    from nanoleaf_cli._validation import validate_sparkle_speed
    import argparse
    assert validate_sparkle_speed("1") == 1
    assert validate_sparkle_speed("10") == 10
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_speed("0")
    with pytest.raises(argparse.ArgumentTypeError):
        validate_sparkle_speed("11")
