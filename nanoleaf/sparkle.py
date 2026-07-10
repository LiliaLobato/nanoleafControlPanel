"""sparkle.py

Sparkle scatter effect + flicker model for the Nanoleaf lamp (Phase 1 v2).

The current-guard uses SPARKLE scatter driven by the saturation-aware
``flicker_load`` model (calibrated to real flicker onset). All panels share the
same hue/sat; ``calculate_guard_setting`` decides how many panels (K) drop to a
floor brightness so the AGGREGATE flicker load stays within budget while the rest
hold the target brightness — the whole point being to hold higher brightness and
saturation with minimised flicker. The result is a single ``animType:"static"``
payload written in one PUT /effects.

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


def calculate_guard_setting(
    profile: LightProfile,
    floor_pct: int,
    threshold: int,
    num_panels: int,
) -> tuple[int, int, int]:
    """Return ``(K, floor_brightness, ceiling_brightness)`` for the sparkle guard.

    FLICKER-BASED — the driver is the saturation-aware ``flicker_load`` (calibrated
    to real flicker onset), NOT (R+G+B). Every HSB tick we act only when over budget:

        load_ceiling = flicker_load(rgb at the colour + target brightness)
        safe_total   = num_panels * (threshold-5)/100    (aggregate flicker budget)

    ``(0, B, B)`` means within budget → no guard (caller uses set_hsb). Otherwise
    SPARKLE scatter keeps the ceiling (N-K panels) at full target brightness AND
    saturation while dimming K panels, so the aggregate flicker load stays within
    budget — letting the lamp hold higher brightness/saturation with less flicker:

      1. Dim K panels to the configured ``floor_pct`` (ceiling stays at target B).
      2. If that floor can't fit the budget, LOWER THE FLOOR at runtime so (N-K)
         panels stay at full target and K panels sit just low enough (dual lever).
      3. Last resort (essentially never): cap the ceiling brightness too.

    Floor brightness is truncated (``int``) so rendered load never rounds above the
    budgeted value — the flicker invariant holds exactly.
    """
    n = num_panels
    b = profile.brightness
    hue, sat = profile.hue, profile.saturation
    if n <= 0:
        return (0, b, b)

    # Use the ACTUAL rendered flicker_load at each brightness (of the rounded RGB),
    # so the invariant holds exactly despite 0-255 integer rounding.
    load_ceiling = flicker_load(hsb_to_rgb(hue, sat, b))
    if load_ceiling <= 0:
        return (0, b, b)

    safe_total = n * (threshold - 5) / 100.0
    if n * load_ceiling <= safe_total:
        return (0, b, b)  # within budget — no guard needed

    # 1) configured floor_pct, ceiling stays at target brightness
    ff = min(max(floor_pct, 0), 100) / 100.0
    floor_bri = int(b * ff)
    floor_load = flicker_load(hsb_to_rgb(hue, sat, floor_bri))
    denom = load_ceiling - floor_load
    if denom > 0:
        k = math.ceil((n * load_ceiling - safe_total) / denom)
        if k <= n:
            return (k, floor_bri, b)

    # 2) configured floor insufficient (or floor_pct>=100) — lower the floor,
    #    keep the ceiling at target B. Dim the minimum panels so the remaining
    #    ceiling panels alone fit the budget, then drop those K just low enough.
    k = min(n, max(1, math.ceil(n - safe_total / load_ceiling)))
    if k < n:
        floor_load_needed = max(0.0, (safe_total - (n - k) * load_ceiling) / k)
        floor_bri = max_brightness_within_flicker(hue, sat, floor_load_needed)
        return (k, floor_bri, b)

    # 3) last resort — even one panel at the ceiling exceeds the budget: cap it.
    b_cap = max_brightness_within_flicker(hue, sat, safe_total / n)
    return (n, b_cap, b_cap)


# ---------------------------------------------------------------------------
# Flicker model (hardware-calibrated on this lamp)
# ---------------------------------------------------------------------------
# Brightness flicker is driven by per-panel channel drive, NOT total (R+G+B):
# a lone channel flickers around bri ~50, but white (all three) drops to ~30.
# The dominant channel plus 0.34x the other two both hit a load of ~0.5 at
# their measured onset, so:
#     flicker_load = (max_channel + 0.34*(sum - max_channel)) / 255
# The guard sparkles so the AGGREGATE flicker_load stays within the budget
# num_panels*(threshold-5)/100 (dims K panels; the rest hold target brightness).

_FLICKER_OTHER_WEIGHT = 0.34


def flicker_load(rgb: tuple[int, int, int]) -> float:
    """Per-panel brightness-flicker load, 0.0-~1.0. Onset ~0.5 on this lamp.

    Modelled as ``(max_channel + 0.34*(sum - max_channel)) / 255`` — the
    dominant channel plus a fraction of the other two. Calibrated to live
    measurements: pure R/G/B flicker at bri ~50, white at bri ~30 (both -> ~0.5).
    """
    mx = max(rgb)
    return (mx + _FLICKER_OTHER_WEIGHT * (sum(rgb) - mx)) / 255.0


def max_brightness_within_flicker(hue: int, sat: int, safe_load: float) -> int:
    """Highest brightness 0-100 whose rendered flicker_load <= safe_load.

    flicker_load is monotonic in brightness, so scan downward and return the
    first brightness at or under the target — truncation guarantees the rendered
    load never exceeds safe_load (the flicker invariant holds exactly).
    """
    for bri in range(100, -1, -1):
        if flicker_load(hsb_to_rgb(hue, sat, bri)) <= safe_load:
            return bri
    return 0


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
    # step = n//k guarantees sorted_ids[::step] yields >= k elements for k <= n.
    return sorted_ids[::step][:k]


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
    floor_brightness: int,
    ceiling_brightness: int,
    transtime: int,
) -> str:
    """Build the Nanoleaf animData string for animType:"static".

    Format:  <numPanels> <panel1_block> <panel2_block> ...
    Block:   <panelId> 1 <R> <G> <B> 0 <transTime>   (1 frame, W=0)

    Panels in dim_ids render the floor RGB (hue/sat at ``floor_brightness``); the
    rest render the ceiling RGB (hue/sat at ``ceiling_brightness``). Both are
    absolute brightnesses chosen by ``calculate_guard_setting``. Returns a single
    space-separated string with no newlines.
    """
    ceil_rgb = hsb_to_rgb(hue, sat, ceiling_brightness)
    floor_rgb = hsb_to_rgb(hue, sat, floor_brightness)
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
    hue: int,
    sat: int,
    floor_brightness: int,
    ceiling_brightness: int,
    transtime: int,
) -> dict:
    """Return the full write_effect payload dict for the static sparkle effect.

    Dimmed panels render hue/sat at ``floor_brightness``, the rest at
    ``ceiling_brightness`` (both absolute, from ``calculate_guard_setting``).
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
            hue,
            sat,
            floor_brightness,
            ceiling_brightness,
            transtime,
        ),
        "loop": False,
        "palette": [],
    }
