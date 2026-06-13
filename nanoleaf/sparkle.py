"""sparkle.py

Sparkle scatter effect for the Nanoleaf current guard (Phase 1 v2).

All panels share the same hue and saturation as the target profile. Each panel
independently drifts its brightness through a smooth triangle-wave sequence
between floor_pct% and 100% of the target brightness. Panels are phase-offset
by their sorted position in the panel list so they never all peak simultaneously,
distributing peak PSU current across the cycle.

The effect runs entirely on the Nanoleaf device after a single write_effect call.
No per-tick API traffic is needed while it plays.
"""

import colorsys

from controller.config import LightProfile
from nanoleaf.effects import speed_to_transtime

_NUM_FRAMES = 2  # frames per per-panel brightness cycle
# 51-panel lamps crash on 6-frame payloads (~6.7 KB); 2 frames (~2.5 KB) is safe.
# Phase offsets (even panels: floor→ceiling, odd: ceiling→floor) still create
# the staggered brightness variation that prevents simultaneous full-current draw.


def hsb_to_rgb(hue: int, sat: int, brightness: int) -> tuple[int, int, int]:
    """Convert HSB (0-359, 0-100, 0-100) to RGB (0-255 each).

    Uses colorsys.hsv_to_rgb internally; the H range is normalised to [0, 1).
    """
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, sat / 100.0, brightness / 100.0)
    return round(r * 255), round(g * 255), round(b * 255)


def _brightness_sequence(brightness: int, floor_pct: int, num_frames: int) -> list[int]:
    """Return a smooth triangle-wave brightness sequence.

    Ramps from floor up to ceiling then back down over num_frames steps.
    floor = brightness * floor_pct / 100, ceiling = brightness.

    Example (brightness=80, floor_pct=70, num_frames=6):
        floor=56, ceiling=80 → [56, 64, 72, 80, 72, 64]
    """
    floor = round(brightness * floor_pct / 100)
    ceiling = brightness
    half = num_frames // 2
    sequence = []
    for i in range(num_frames):
        t = i / half if i <= half else (num_frames - i) / max(1, num_frames - half)
        val = round(floor + (ceiling - floor) * t)
        sequence.append(max(0, min(100, val)))
    return sequence


def build_sparkle_animdata(
    panel_ids: list[int],
    hue: int,
    sat: int,
    brightness: int,
    floor_pct: int,
    speed: int,
) -> str:
    """Build the Nanoleaf animData string for the sparkle scatter effect.

    Format per panel: <panelId> <numFrames> <R> <G> <B> <W> <transTime> [...]
    Full string:      <numPanels> <panel1_data> <panel2_data> ...

    W (white channel) is always 0 for coloured panels.
    Phase offset: sorted_panel_index % num_frames — each panel starts at a
    different point in the brightness cycle so no group of panels peaks together.

    Returns a single space-separated string with no newlines.
    """
    trans = speed_to_transtime(speed)
    sequence = _brightness_sequence(brightness, floor_pct, _NUM_FRAMES)
    sorted_ids = sorted(panel_ids)

    tokens: list[str] = [str(len(sorted_ids))]
    for sorted_index, pid in enumerate(sorted_ids):
        offset = sorted_index % _NUM_FRAMES
        frames = sequence[offset:] + sequence[:offset]
        tokens.append(str(pid))
        tokens.append(str(_NUM_FRAMES))
        for bri in frames:
            r, g, b = hsb_to_rgb(hue, sat, bri)
            tokens += [str(r), str(g), str(b), "0", str(trans)]

    return " ".join(tokens)


def build_sparkle_effect(
    panel_ids: list[int],
    profile: LightProfile,
    floor_pct: int,
    speed: int,
) -> dict:
    """Return the full write_effect payload dict for the sparkle scatter effect.

    Uses command='display' (volatile — runs immediately, no NVRAM write).
    animType='custom' with raw animData. loop=True so the device cycles
    the brightness sequence indefinitely until the next write_effect call.
    """
    return {
        "command": "display",
        "animType": "custom",
        "animData": build_sparkle_animdata(
            panel_ids,
            profile.hue,
            profile.saturation,
            profile.brightness,
            floor_pct,
            speed,
        ),
        "loop": True,
        "palette": [],
    }
