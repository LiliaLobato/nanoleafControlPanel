"""Shared fixtures and helpers for the controller test suite."""

import json
from pathlib import Path

import pytest

from weather.openWeather import OpenWeatherLight

FIXTURES = Path(__file__).parent / "fixtures"

# NOAA math coordinates for Bellevue, WA; separate from fixture coord data.
LAT = 47.6144
LON = -122.1923

# Real-shaped 51-panel layout: ids 1-52 minus the Rhythm module (id 6). Matches
# the production lamp so K-count and even-spacing exercise realistic ordering
# rather than a tiny stub. Shared by the sparkle unit tests and the day-sim e2e.
PANELS_51 = [i for i in range(1, 53) if i != 6]


class MockLamp:
    """In-memory Nanoleaf stand-in shared by the sparkle tests and the day-sim e2e.

    Records every lamp API call in ``calls`` and reflects the last applied state
    via ``get_full_state``. Optional constructor flags inject the failures the
    guard's degrade/backoff paths need:

      panel_ids        — override the panel layout (default: PANELS_51)
      panel_ids_raises — get_panel_ids() raises; get_full_state reports [] panels
      write_ok         — write_effect() return value (False = lamp rejected payload)
      write_raises     — exception write_effect() raises (transient-failure path)
    """

    DEFAULT_PANEL_IDS = list(PANELS_51)

    def __init__(self, panel_ids=None, panel_ids_raises=False, write_ok=True,
                 write_raises=None):
        self._state = {"on": False, "hue": 0, "sat": 0, "brightness": 0,
                       "ct": 4000, "colorMode": "hs"}
        self.calls: list = []
        self._panel_ids = list(self.DEFAULT_PANEL_IDS if panel_ids is None else panel_ids)
        self._panel_ids_raises = panel_ids_raises
        self._write_ok = write_ok
        self._write_raises = write_raises

    def get_full_state(self, retries=2, retry_delay=10.0, with_panels=False) -> dict:
        s = dict(self._state)
        if with_panels:
            # Mirrors the real lamp: a missing/unreachable layout yields [].
            s["panel_ids"] = [] if self._panel_ids_raises else list(self._panel_ids)
        return s

    def get_panel_ids(self) -> list:
        self.calls.append(("get_panel_ids",))
        if self._panel_ids_raises:
            raise RuntimeError("layout unreachable")
        return list(self._panel_ids)

    def write_effect(self, effect: dict) -> bool:
        self.calls.append(("write_effect", effect))
        if self._write_raises is not None:
            raise self._write_raises
        if self._write_ok:
            # A live static effect reports colorMode "effect"; GET /state then
            # returns DEFAULT color fields, not the rendered colors (firmware).
            self._state.update({"colorMode": "effect", "hue": 0, "sat": 0,
                                "ct": 1200, "brightness": 20})
        return self._write_ok

    def set_hsb(self, hue: int, sat: int, bri: int, on: bool | None = None) -> bool:
        self.calls.append(("set_hsb", hue, sat, bri, on))
        self._state.update({"hue": hue, "sat": sat, "brightness": bri, "colorMode": "hs"})
        if on is not None:
            self._state["on"] = on
        return True

    def set_color_temp_and_brightness(self, ct: int, bri: int, on: bool | None = None) -> bool:
        self.calls.append(("set_ct", ct, bri, on))
        self._state.update({"ct": ct, "brightness": bri, "colorMode": "ct"})
        if on is not None:
            self._state["on"] = on
        return True

    def power_on(self) -> bool:
        self.calls.append(("power_on",))
        self._state["on"] = True
        return True

    def power_off(self) -> bool:
        self.calls.append(("power_off",))
        self._state["on"] = False
        return True

    def names(self) -> list:
        """Ordered list of called method names (ignoring args) — handy in asserts."""
        return [c[0] for c in self.calls]


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_instance(fixture_name: str) -> OpenWeatherLight:
    """Build an OpenWeatherLight from a fixture via from_cache (no network)."""
    return OpenWeatherLight.from_cache(_load_fixture(fixture_name), LAT, LON)


@pytest.fixture
def clear():
    return _make_instance("clear.json")


@pytest.fixture
def rain():
    return _make_instance("rain.json")


@pytest.fixture
def snow():
    return _make_instance("snow.json")


@pytest.fixture
def fog():
    return _make_instance("fog.json")


@pytest.fixture
def overcast():
    return _make_instance("overcast.json")


@pytest.fixture
def partly_cloudy():
    return _make_instance("partly_cloudy.json")


@pytest.fixture
def thunderstorm():
    return _make_instance("thunderstorm.json")


