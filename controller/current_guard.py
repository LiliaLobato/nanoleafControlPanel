"""current_guard.py

Decides how the flicker current-guard responds to a tick's target colour,
separate from the controller's lamp-I/O and failure-handling in run().

For a colour over the lamp's flicker budget there are three outcomes:
  - sparkle — dim K panels (K <= sparkle_max_dim_panels) to a floor while the
              ceiling holds target (HSB with known panel IDs)
  - cap     — lower brightness to the flicker-safe maximum (CT, or the
              no-panel-IDs HSB fallback — neither can be expressed per-panel)
  - none    — the colour is within budget; apply it unchanged

``evaluate_guard`` is the single decision point. It mutates ``state`` (the
panel_ids cache + sparkle selection bookkeeping) exactly as the old inline
controller block did and returns a GuardDecision; the caller performs the
actual lamp writes.
"""

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from controller.config import Config, LightProfile
from nanoleaf.sparkle import (
    build_sparkle_effect,
    calculate_guard_setting,
    flicker_budget,
    max_brightness_within_flicker,
    select_dim_panels,
)

logger = logging.getLogger(__name__)


@dataclass
class GuardDecision:
    """Outcome of the current-guard for one tick.

    effective_color — the colour to apply (possibly brightness-capped)
    sparkle_effect  — write_effect payload when sparkling, else None
    guard_active    — "sparkle" | "brightness_cap" | None (the last_applied tag)
    sorted_ids      — sorted panel IDs used for the sparkle (for logging)
    dim_ids         — the dimmed subset (for logging)
    """
    effective_color: LightProfile
    sparkle_effect: Optional[dict] = None
    guard_active: Optional[str] = None
    sorted_ids: list[int] = field(default_factory=list)
    dim_ids: list[int] = field(default_factory=list)


def evaluate_guard(
    effective_color: LightProfile,
    guard_on: bool,
    light_state: dict,
    state: dict,
    config: Config,
    now: datetime,
) -> GuardDecision:
    """Decide the guard response for this tick (see module docstring).

    ``guard_on`` = ``config.current_guard_enabled AND should_be_on`` — the guard
    acts only when both hold. Mutates ``state``: panel_ids cache,
    sparkle_dim_panels / sparkle_last_rotation_at, sparkle_effect_hash.
    """
    if not guard_on:
        state["sparkle_effect_hash"] = None
        if not config.current_guard_enabled:
            logger.debug("current_guard: disabled")
        else:  # enabled but should_be_on is False
            logger.debug("current_guard: skipped — lamp going off")
        return GuardDecision(effective_color)

    if effective_color.mode == "hsb":
        return _evaluate_hsb(effective_color, light_state, state, config, now)

    # CT cannot be expressed per-panel in animData, so it can't sparkle. Treat CT
    # as worst-case white and cap to the flicker-safe brightness — near-white
    # flickers above ~bri 30, so this caps lower than the old flat threshold-5.
    decision = _flicker_cap(effective_color, 0, 0, config, "CT")
    state["sparkle_effect_hash"] = None
    return decision


def _evaluate_hsb(
    effective_color: LightProfile,
    light_state: dict,
    state: dict,
    config: Config,
    now: datetime,
) -> GuardDecision:
    """HSB branch: sparkle if panels are known and over budget, else cap/pass."""
    # Panels come from the SAME get_info() as the state read (via
    # get_full_state(with_panels=True)) — zero extra device GETs per tick, which
    # matters because the guard runs on every high-consumption tick. Reconcile the
    # live set with the cache; on a layout change clear the dim selection so
    # select_dim_panels re-derives it deterministically (even-spacing, not a
    # random reshuffle — no flicker).
    cached_ids = state.get("panel_ids") or []
    live_ids = light_state.get("panel_ids") or []
    panel_ids = live_ids or cached_ids
    if live_ids and set(live_ids) != set(cached_ids):
        if cached_ids:
            logger.info(
                "current_guard: panel set changed (%d → %d) — resetting dim selection",
                len(cached_ids), len(live_ids),
            )
            state["sparkle_dim_panels"] = []
            state["sparkle_last_rotation_at"] = None
        state["panel_ids"] = live_ids

    sparkle_override = (state.get("party_mode") or {}).get("sparkle_override", {})
    floor_pct = sparkle_override.get("floor_pct", config.sparkle_floor_pct)
    k, floor_bri, ceiling_bri = calculate_guard_setting(
        effective_color, floor_pct, config.current_guard_threshold, len(panel_ids),
        config.sparkle_max_dim_panels,
    )

    if panel_ids and k > 0:
        sorted_ids = sorted(panel_ids)
        dim_ids = select_dim_panels(state, sorted_ids, k, now, config)
        sparkle_effect = build_sparkle_effect(
            sorted_ids, dim_ids, effective_color.hue, effective_color.saturation,
            floor_bri, ceiling_bri, config.sparkle_transtime,
        )
        logger.debug(
            "current_guard: sparkle K=%d (floor=%d ceiling=%d)", k, floor_bri, ceiling_bri,
        )
        return GuardDecision(
            effective_color, sparkle_effect=sparkle_effect, guard_active="sparkle",
            sorted_ids=sorted_ids, dim_ids=dim_ids,
        )

    if panel_ids:
        # Within budget (K=0) — apply normally, no sparkle, no cap.
        logger.debug("current_guard: HSB within budget (K=0) — no sparkle, no cap")
        state["sparkle_effect_hash"] = None
        return GuardDecision(effective_color)

    # No panel IDs — cannot scatter. Cap this colour to its flicker-safe
    # brightness (cap DOWN only: never raise a dim, within-budget colour).
    decision = _flicker_cap(
        effective_color, effective_color.hue, effective_color.saturation,
        config, "no panel IDs —",
    )
    state["sparkle_effect_hash"] = None
    return decision


def _flicker_cap(
    effective_color: LightProfile,
    cap_hue: int,
    cap_sat: int,
    config: Config,
    label: str,
) -> GuardDecision:
    """Cap brightness to the flicker-safe max for (cap_hue, cap_sat). Cap DOWN only.

    Shared by the CT and no-panel-IDs branches — flicker_load is monotonic in
    brightness so this never raises a within-budget colour.
    """
    safe_bri = max_brightness_within_flicker(
        cap_hue, cap_sat, flicker_budget(config.current_guard_threshold)
    )
    if effective_color.brightness > safe_bri:
        logger.info(
            "current_guard: %s flicker-capping brightness %d → %d",
            label, effective_color.brightness, safe_bri,
        )
        return GuardDecision(
            dataclasses.replace(effective_color, brightness=safe_bri),
            guard_active="brightness_cap",
        )
    return GuardDecision(effective_color)
