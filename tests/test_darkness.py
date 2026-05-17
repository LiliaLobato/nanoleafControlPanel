"""Tests for OpenWeatherLight.is_dark_outside()."""

from datetime import datetime, timezone

import pytest

from weather.openWeather import OpenWeatherLight
from tests.conftest import _load_fixture, _make_instance, LAT, LON

# Shared datetimes used across tests
SUMMER_NOON_UTC = datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc)   # ~62 deg elevation
WINTER_NOON_UTC = datetime(2025, 12, 21, 20, 0, 0, tzinfo=timezone.utc)  # ~19 deg elevation
PRE_DAWN_UTC = datetime(2025, 3, 20, 13, 30, 0, tzinfo=timezone.utc)     # ~-8 deg elevation (below horizon)


class TestIsDarkOutside:
    """Truth table: (sun position) x (weather conditions) -> dark or not."""

    @pytest.mark.parametrize("description, fixture_name, at", [
        (
            "high sun + clear sky (summer noon, ~62 deg, 5% clouds)",
            "clear.json",
            SUMMER_NOON_UTC,
        ),
        (
            "high sun + overcast (summer noon, ~62 deg, well above 20 deg threshold)",
            "overcast.json",
            SUMMER_NOON_UTC,
        ),
        (
            "low sun + clear sky (winter noon, ~19 deg, clear non-adverse)",
            "clear.json",
            WINTER_NOON_UTC,
        ),
        (
            "low sun + adverse but low clouds (winter noon, rain at 50% clouds, below 75% threshold)",
            "rain.json",
            WINTER_NOON_UTC,
        ),
    ])
    def test_not_dark_scenarios(self, description, fixture_name, at):
        if fixture_name == "rain.json" and "50%" in description:
            data = _load_fixture(fixture_name)
            data["clouds"]["all"] = 50
            w = OpenWeatherLight.from_cache(data, LAT, LON)
        else:
            w = _make_instance(fixture_name)

        result = w.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=at)
        assert result is False, (
            f"Expected is_dark_outside() to be False for: {description}, "
            f"but got True; check elevation, adverse condition, and cloud threshold logic."
        )

    def test_below_horizon_always_dark(self, clear):
        """Sun below horizon gives dark regardless of weather."""
        result = clear.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=PRE_DAWN_UTC)
        assert result is True, (
            f"Expected is_dark_outside() to be True when sun is below the horizon (pre-dawn), "
            f"but got False; elevation < 0 must always return dark."
        )

    def test_low_sun_overcast_is_dark(self):
        """Low sun (but above horizon) + overcast + high clouds gives dark."""
        data = _load_fixture("overcast.json")
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        # Winter noon: elevation ~19 deg (below 20 deg threshold)
        # overcast (804) is adverse, clouds=95% > 75% threshold, elevation ~19 deg < 20 deg
        result = w.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=WINTER_NOON_UTC)
        assert result is True, (
            f"Expected is_dark_outside() to be True for low sun + overcast + high clouds "
            f"(winter noon, ~19 deg elevation, 95% clouds, adverse), "
            f"but got False; all three dark conditions are met."
        )
