"""sparkle.py

Sparkle scatter effect for the Nanoleaf current guard (Phase 1 v2).

All panels share the same hue and saturation as the target profile.
Panels alternate between ceiling brightness (even sorted index) and floor
brightness (odd sorted index), creating a static scattered-brightness pattern
that prevents simultaneous full-current draw across all 51 panels.

Animated multi-frame payloads (2–6 frames × 51 panels) lock up the Nanoleaf's
embedded HTTP server for ~2 minutes then crash the device. A single frame per
panel (~350 tokens, ~1.5 KB) is the maximum this device can handle.
"""

import colorsys

from controller.config import LightProfile
from nanoleaf.effects import speed_to_transtime

_NUM_FRAMES = 1  # static: 1 frame per panel
# Animated payloads (2+ frames × 51 panels) crash the Nanoleaf firmware.
# Even/odd sorted_index alternation gives visible brightness variation
# and achieves the current-guard goal without animation.


def hsb_to_rgb(hue: int, sat: int, brightness: int) -> tuple[int, int, int]:
    """Convert HSB (0-359, 0-100, 0-100) to RGB (0-255 each)."""
    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, sat / 100.0, brightness / 100.0)
    return round(r * 255), round(g * 255), round(b * 255)


def _brightness_sequence(brightness: int, floor_pct: int, num_frames: int) -> list[int]:
    """Return a smooth triangle-wave brightness sequence.

    Ramps from floor up to ceiling then back down over num_frames steps.
    floor = brightness * floor_pct / 100, ceiling = brightness.

    Example (brightness=80, floor_pct=70, num_frames=6):
        floor=56, ceiling=80 → [56, 64, 72, 80, 72, 64]
    """
    if num_frames <= 1:
        return [brightness]
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

    Format per panel: <panelId> <numFrames> <R> <G> <B> <W> <transTime>
    Full string:      <numPanels> <panel1_data> <panel2_data> ...

    W (white channel) is always 0 for coloured panels.
    Even sorted_index → ceiling brightness; odd → floor brightness.

    Returns a single space-separated string with no newlines.
    """
    trans = speed_to_transtime(speed)
    floor_bri = round(brightness * floor_pct / 100)
    ceiling_bri = brightness
    sorted_ids = sorted(panel_ids)

    tokens: list[str] = [str(len(sorted_ids))]
    for sorted_index, pid in enumerate(sorted_ids):
        bri = ceiling_bri if sorted_index % 2 == 0 else floor_bri
        r, g, b = hsb_to_rgb(hue, sat, bri)
        tokens.append(str(pid))
        tokens.append(str(_NUM_FRAMES))
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
    animType='custom' with raw animData. Single frame per panel = static.
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
        "loop": False,
        "palette": [],
    }
