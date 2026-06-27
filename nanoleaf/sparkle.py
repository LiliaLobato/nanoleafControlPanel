"""sparkle.py

Sparkle scatter effect for the Nanoleaf current guard (Phase 1 v2).

All panels share the same hue and saturation as the target profile. A
power-budget calculation (``calculate_dim_count``) decides how many panels (K)
must drop to a floor brightness so the lamp's total current draw stays within
the PSU budget; the remaining (N − K) stay at the ceiling (target) brightness.
The result is a single ``animType:"static"`` payload written in one PUT /effects.

Why static, not custom: animated multi-frame ``animType:"custom"`` payloads lock
up the Nanoleaf firmware (5.3.2) at high panel counts. ``animType:"static"`` uses
a different firmware code path — it sets panel colors once and stops — and is
stable for all 51 panels. Each panel carries a single frame with a fade-in
``transTime`` (config ``sparkle_transtime``); the fade only shows on color change.

Dim-panel selection is two-mode: deterministic even-spacing while K changes
(brightness ramps, no visible snap) and a random reshuffle every
``sparkle_rotation_interval`` ticks for wear levelling.
"""

import colorsys
import math
import random

from controller.config import LightProfile
from controller.dateTime import parse_iso


def hsb_to_rgb(hue: int, sat: int, brightness: int) -> tuple[int, int, int]:
    """Convert HSB (0-359, 0-100, 0-100) to RGB (0-255 each)."""
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, sat / 100.0, brightness / 100.0)
    return round(r * 255), round(g * 255), round(b * 255)


def power_fraction(rgb: tuple[int, int, int]) -> float:
    """Per-panel current draw as a fraction 0.0-1.0, modelled as (R+G+B)/765.

    PWM per channel means average current is proportional to channel value, so
    warm colours (low green/blue) draw far less than white at the same brightness.
    """
    return sum(rgb) / 765.0


def calculate_dim_count(
    profile: LightProfile,
    floor_pct: int,
    threshold: int,
    num_panels: int,
) -> int:
    """Return K — how many panels must drop to floor brightness for the guard.

    Power model (all values are per-panel current fractions 0.0-1.0):
        power        = (R+G+B)/765 at the ceiling (target) colour
        floor_power  = power * floor_pct/100
        safe_total   = num_panels * (threshold-5)/100   (budget = all panels at cap)
        actual_total = num_panels * power
        K            = ceil((actual_total - safe_total) / (power - floor_power))

    Returns 0 (no sparkle needed — caller falls back to set_hsb) when:
      - num_panels <= 0
      - the colour is already within budget (actual_total <= safe_total) — common
        for warm sunrise/sunset colours
      - floor_pct >= 100 (floor == ceiling, nothing to dim — avoids div-by-zero)
      - power <= 0 (black)
    K is clamped to num_panels.
    """
    if num_panels <= 0 or floor_pct >= 100:
        return 0

    rgb = hsb_to_rgb(profile.hue, profile.saturation, profile.brightness)
    power = power_fraction(rgb)
    if power <= 0:
        return 0

    safe_total = num_panels * (threshold - 5) / 100.0
    actual_total = num_panels * power
    if actual_total <= safe_total:
        return 0

    floor_power = power * floor_pct / 100.0
    denom = power - floor_power
    if denom <= 0:
        return 0

    k = math.ceil((actual_total - safe_total) / denom)
    return max(0, min(k, num_panels))


def even_spaced(sorted_ids: list[int], k: int) -> list[int]:
    """Pick k panels spread evenly across sorted_ids (deterministic, no RNG).

    Guards step==0 (k > len) and short slices so the result always has exactly
    min(k, len) ids.
    """
    n = len(sorted_ids)
    k = max(0, min(k, n))
    if k == 0:
        return []
    step = max(1, n // k)
    selection = sorted_ids[::step][:k]
    if len(selection) < k:                       # step too large near the tail
        selection = sorted_ids[:k]
    return selection


def select_dim_panels(
    state: dict,
    sorted_ids: list[int],
    k: int,
    now,
    config,
) -> list[int]:
    """Choose which k panels render at floor brightness; mutates state.

    Two modes:
      - K changed (brightness ramp) → deterministic even-spacing, no reshuffle
        (prevents a visible snap when different panels would otherwise be picked
        every tick). Resets the rotation clock.
      - K unchanged → reuse the stored selection until sparkle_rotation_interval
        cron ticks have elapsed, then random.sample for wear levelling.

    Stores ``sparkle_dim_panels`` and ``sparkle_last_rotation_at`` in state.
    """
    n = len(sorted_ids)
    k = max(0, min(k, n))
    if k == 0:
        state["sparkle_dim_panels"] = []
        return []

    stored = [p for p in (state.get("sparkle_dim_panels") or []) if p in sorted_ids]

    if len(stored) != k:
        selection = even_spaced(sorted_ids, k)
        state["sparkle_dim_panels"] = selection
        state["sparkle_last_rotation_at"] = now.isoformat()
        return selection

    rotate = True
    last_rotation_raw = state.get("sparkle_last_rotation_at")
    if last_rotation_raw:
        try:
            ticks_since = (now - parse_iso(last_rotation_raw)).total_seconds() / (
                config.cron_interval_minutes * 60
            )
            rotate = ticks_since >= config.sparkle_rotation_interval
        except (ValueError, TypeError):
            rotate = True

    if rotate:
        selection = random.sample(sorted_ids, k)
        state["sparkle_dim_panels"] = selection
        state["sparkle_last_rotation_at"] = now.isoformat()
        return selection

    return stored


def build_sparkle_animdata(
    panel_ids: list[int],
    dim_ids: list[int],
    hue: int,
    sat: int,
    brightness: int,
    floor_pct: int,
    transtime: int,
) -> str:
    """Build the Nanoleaf animData string for animType:"static".

    Format:  <numPanels> <panel1_block> <panel2_block> ...
    Block:   <panelId> 1 <R> <G> <B> 0 <transTime>   (1 frame, W=0)

    Panels in dim_ids render floor RGB (brightness*floor_pct/100); the rest
    render the ceiling RGB at the target brightness. Returns a single
    space-separated string with no newlines.
    """
    floor_bri = round(brightness * floor_pct / 100)
    ceil_rgb = hsb_to_rgb(hue, sat, brightness)
    floor_rgb = hsb_to_rgb(hue, sat, floor_bri)
    dim_set = set(dim_ids)
    sorted_ids = sorted(panel_ids)

    tokens: list[str] = [str(len(sorted_ids))]
    for pid in sorted_ids:
        r, g, b = floor_rgb if pid in dim_set else ceil_rgb
        tokens += [str(pid), "1", str(r), str(g), str(b), "0", str(transtime)]
    return " ".join(tokens)


def build_sparkle_effect(
    panel_ids: list[int],
    dim_ids: list[int],
    profile: LightProfile,
    floor_pct: int,
    transtime: int,
) -> dict:
    """Return the full write_effect payload dict for the static sparkle effect.

    Uses command='display' (volatile — runs immediately, no NVRAM write) and
    animType='static' (firmware-stable for all 51 panels). version '2.0' is
    required by firmware 5.3.2 or the effect is silently ignored.
    """
    return {
        "command": "display",
        "version": "2.0",
        "animType": "static",
        "animData": build_sparkle_animdata(
            panel_ids,
            dim_ids,
            profile.hue,
            profile.saturation,
            profile.brightness,
            floor_pct,
            transtime,
        ),
        "loop": False,
        "palette": [],
    }
