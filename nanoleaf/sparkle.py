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


def calculate_guard_setting(
    profile: LightProfile,
    floor_pct: int,
    threshold: int,
    num_panels: int,
) -> tuple[int, int, int]:
    """Return ``(K, floor_brightness, ceiling_brightness)`` for the current guard.

    POWER-BASED — there is no brightness trigger. Every HSB tick we compute the
    colour's actual per-panel draw and act only when the lamp is over budget:

        p_full       = (R+G+B)/765 at the colour, brightness 100  (draw scales ∝ brightness)
        safe_total   = num_panels * (threshold-5)/100             (the power budget)
        power_ceiling= p_full * brightness/100                    (per-panel draw at target)

    ``(0, B, B)`` means within budget → no guard (caller uses set_hsb). Otherwise
    three escalating responses, always keeping total draw ≤ safe_total:

      1. Dim K panels to the configured ``floor_pct`` (ceiling stays at target B).
      2. If that floor can't bring it within budget, LOWER THE FLOOR at runtime so
         (N-K) panels stay at full target brightness and K panels sit just low
         enough to meet the budget (keeps maximum brightness — the dual lever).
      3. Last resort (essentially never, e.g. one panel already over budget):
         cap the ceiling brightness.

    Floor brightness is truncated (``int``) so the rendered draw never rounds
    above the budgeted value — the budget invariant always holds.
    """
    n = num_panels
    b = profile.brightness
    hue, sat = profile.hue, profile.saturation
    if n <= 0:
        return (0, b, b)

    # Use ACTUAL rendered power at each brightness (power_fraction of the rounded
    # RGB), not a linear model — RGB channels are integers 0-255, so the linear
    # approximation can drift a hair above budget. Computing against the real
    # rendered values makes the budget invariant hold exactly.
    power_ceiling = power_fraction(hsb_to_rgb(hue, sat, b))
    if power_ceiling <= 0:
        return (0, b, b)

    safe_total = n * (threshold - 5) / 100.0
    if n * power_ceiling <= safe_total:
        return (0, b, b)  # within budget — no guard needed

    # 1) configured floor_pct, ceiling stays at target brightness
    ff = min(max(floor_pct, 0), 100) / 100.0
    floor_bri = int(b * ff)
    floor_power = power_fraction(hsb_to_rgb(hue, sat, floor_bri))
    denom = power_ceiling - floor_power
    if denom > 0:
        k = math.ceil((n * power_ceiling - safe_total) / denom)
        if k <= n:
            return (k, floor_bri, b)

    # 2) configured floor insufficient (or floor_pct>=100) — lower the floor,
    #    keep the ceiling at target B. Dim the minimum panels so the remaining
    #    ceiling panels alone fit the budget, then drop those K just low enough.
    k = min(n, max(1, math.ceil(n - safe_total / power_ceiling)))
    if k < n:
        floor_power_needed = max(0.0, (safe_total - (n - k) * power_ceiling) / k)
        floor_bri = _max_brightness_within_power(hue, sat, floor_power_needed)
        return (k, floor_bri, b)

    # 3) last resort — even one panel at the ceiling exceeds the budget: cap it.
    b_cap = _max_brightness_within_power(hue, sat, safe_total / n)
    return (n, b_cap, b_cap)


def _max_brightness_within_power(hue: int, sat: int, target_power: float) -> int:
    """Highest brightness 0-100 whose rendered per-panel power <= target_power.

    Power is monotonic in brightness, so scan downward and return the first
    brightness at or under the target — guarantees the rendered draw never
    exceeds the budgeted value.
    """
    for bri in range(100, -1, -1):
        if power_fraction(hsb_to_rgb(hue, sat, bri)) <= target_power:
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
