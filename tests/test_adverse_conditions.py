"""Tests for OpenWeatherLight.has_adverse_conditions()."""

import pytest

from weather.openWeather import OpenWeatherLight
from tests.conftest import _load_fixture, _make_instance, LAT, LON


class TestHasAdverseConditions:
    @pytest.mark.parametrize("fixture_name, expected", [
        ("clear.json", False),           # 800 Clear
        ("partly_cloudy.json", False),   # 802 scattered clouds
        ("rain.json", True),             # 501 moderate rain
        ("snow.json", True),             # 601 snow
        ("fog.json", True),              # 741 fog
        ("overcast.json", True),         # 804 overcast clouds
        ("thunderstorm.json", True),     # 211 thunderstorm
    ])
    def test_condition_codes(self, fixture_name, expected):
        w = _make_instance(fixture_name)
        assert w.has_adverse_conditions() is expected, (
            f"Expected has_adverse_conditions() to be {expected} for {fixture_name}, "
            f"but got {w.has_adverse_conditions()}; check condition code mapping."
        )

    def test_few_clouds_not_adverse(self):
        """Code 801 (few clouds) should NOT be adverse."""
        data = _load_fixture("clear.json")
        data["weather"][0]["id"] = 801
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        assert w.has_adverse_conditions() is False, (
            "Expected code 801 (few clouds) to be non-adverse, "
            f"but has_adverse_conditions() returned True; 801 must not be in adverse ranges."
        )

    @pytest.mark.parametrize("code, label", [
        (803, "broken clouds"),
        (300, "drizzle"),
    ])
    def test_boundary_codes_adverse(self, code, label):
        """Boundary codes 803 (broken clouds) and 300 (drizzle) should be adverse."""
        data = _load_fixture("clear.json")
        data["weather"][0]["id"] = code
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        assert w.has_adverse_conditions() is True, (
            f"Expected code {code} ({label}) to be adverse, "
            f"but has_adverse_conditions() returned False; check adverse code ranges and _ADVERSE_CLOUD_CODES."
        )
