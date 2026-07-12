"""effects.py

Shared utilities for Nanoleaf effect builders. Retained for Phase 2 (the
``wheel`` animation) — not yet wired into any caller. Phase 1 sparkle drives
transTime from the ``sparkle_transtime`` config knob directly and does NOT use
this module.
"""


def speed_to_transtime(speed: int) -> int:
    """Map speed 1–10 to Nanoleaf transTime units (each unit = 100ms).

    Speed 1 → transTime 5 (500ms/frame, slowest shimmer).
    Speed 10 → transTime 2 (200ms/frame, fastest shimmer).
    Minimum is 2 — transTime 1 (100ms) is 10Hz and crosses into visible flicker.

    Mapping (linear interpolation, rounded):
        1→5  2→5  3→4  4→4  5→4  6→3  7→3  8→3  9→2  10→2
    """
    speed = max(1, min(10, speed))
    return max(2, round(5 - (speed - 1) * 3 / 9))
