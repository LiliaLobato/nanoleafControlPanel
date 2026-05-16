"""interpolation.py

Pure math interpolation helpers for the Nanoleaf controller.
"""

from config import LightProfile


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_hue(start: int, end: int, t: float) -> int:
    """Shortest-path hue interpolation around the 360° color wheel.

    Travels the short way: 350→15 covers 25°, not 335°.
    """
    diff = (end - start) % 360
    if diff > 180:
        diff -= 360
    return round((start + diff * t) % 360)


def interpolate_profiles(start: LightProfile, end: LightProfile, t: float) -> LightProfile:
    """Interpolate between two light profiles at position t (0.0–1.0).

    Rules (first match wins):
    1. Fade to off (end.brightness==0): hold source color, ramp brightness only
    2. Fade from off (start.brightness==0): snap to target color, ramp brightness only
    3. Cross-mode (CT↔HSB): snap to target color, ramp brightness only
    4. Same mode CT: lerp ct + brightness
    5. Same mode HSB: lerp_hue(hue) + lerp sat + lerp brightness
    """
    if end.brightness == 0:
        return LightProfile(
            mode=start.mode, hue=start.hue, saturation=start.saturation,
            color_temp=start.color_temp,
            brightness=round(_lerp(start.brightness, 0, t)),
        )
    if start.brightness == 0:
        return LightProfile(
            mode=end.mode, hue=end.hue, saturation=end.saturation,
            color_temp=end.color_temp,
            brightness=round(_lerp(0, end.brightness, t)),
        )
    if start.mode != end.mode:
        return LightProfile(
            mode=end.mode, hue=end.hue, saturation=end.saturation,
            color_temp=end.color_temp,
            brightness=round(_lerp(start.brightness, end.brightness, t)),
        )
    if start.mode == "ct":
        return LightProfile(
            mode="ct",
            color_temp=round(_lerp(start.color_temp, end.color_temp, t)),
            brightness=round(_lerp(start.brightness, end.brightness, t)),
        )
    return LightProfile(
        mode="hsb",
        hue=lerp_hue(start.hue, end.hue, t),
        saturation=round(_lerp(start.saturation, end.saturation, t)),
        brightness=round(_lerp(start.brightness, end.brightness, t)),
    )
