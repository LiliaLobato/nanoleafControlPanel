"""Tests for sunrise/sunset datetime methods and adjusted sunset logic."""

import pytest

from openWeather import OpenWeatherLight
from tests.conftest import _load_fixture, _make_instance, LAT, LON


class TestSunriseSunset:
    """All fixtures share the same timestamps: sunrise 1750504260, sunset 1750561860."""

    def test_sunrise_and_sunset_are_tz_aware(self, clear):
        """Both get_sunrise_dt() and get_sunset_dt() must return tz-aware datetimes."""
        sunrise = clear.get_sunrise_dt()
        sunset = clear.get_sunset_dt()
        assert sunrise.tzinfo is not None, (
            "Expected get_sunrise_dt() to return a tz-aware datetime, "
            f"but tzinfo was None; the method must attach a timezone."
        )
        assert sunset.tzinfo is not None, (
            "Expected get_sunset_dt() to return a tz-aware datetime, "
            f"but tzinfo was None; the method must attach a timezone."
        )

    def test_sunrise_and_sunset_correct_local_time(self, clear):
        """Sunrise: 2025-06-21 04:11 PDT. Sunset: 2025-06-21 20:11 PDT."""
        sunrise = clear.get_sunrise_dt()
        sunset = clear.get_sunset_dt()

        assert (sunrise.year, sunrise.month, sunrise.day) == (2025, 6, 21), (
            f"Expected sunrise date 2025-06-21, got {sunrise.date()}; "
            "check that timestamp 1750504260 converts correctly to local time."
        )
        assert (sunrise.hour, sunrise.minute) == (4, 11), (
            f"Expected sunrise time 04:11, got {sunrise.hour:02d}:{sunrise.minute:02d}; "
            "check timezone conversion from UTC to LOCAL_TZ."
        )

        assert (sunset.year, sunset.month, sunset.day) == (2025, 6, 21), (
            f"Expected sunset date 2025-06-21, got {sunset.date()}; "
            "check that timestamp 1750561860 converts correctly to local time."
        )
        assert (sunset.hour, sunset.minute) == (20, 11), (
            f"Expected sunset time 20:11, got {sunset.hour:02d}:{sunset.minute:02d}; "
            "check timezone conversion from UTC to LOCAL_TZ."
        )

    def test_sunrise_before_sunset(self, clear):
        assert clear.get_sunrise_dt() < clear.get_sunset_dt(), (
            "Expected sunrise to occur before sunset, "
            "but get_sunrise_dt() >= get_sunset_dt(); check timestamp values in fixture."
        )


class TestAdjustedSunset:
    def test_no_offset_below_threshold(self, clear):
        """Clear sky (5% clouds): no adjustment regardless of thresholds."""
        raw = clear.get_sunset_dt()
        adjusted = clear.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        assert adjusted == raw, (
            f"Expected no sunset adjustment for clear sky (5% clouds), "
            f"but raw={raw} and adjusted={adjusted} differ; "
            "has_adverse_conditions() must be False for clear sky."
        )

    def test_no_offset_non_adverse_above_threshold(self, partly_cloudy):
        """Partly cloudy (802, 40% clouds): not adverse, no adjustment."""
        raw = partly_cloudy.get_sunset_dt()
        adjusted = partly_cloudy.get_adjusted_sunset(cloud_threshold=30, offset_min=30, offset_max=75)
        assert adjusted == raw, (
            f"Expected no sunset adjustment for partly cloudy (802, non-adverse), "
            f"but raw={raw} and adjusted={adjusted} differ; "
            "code 802 must not trigger adverse conditions."
        )

    def test_offset_at_threshold(self):
        """Adverse at exactly threshold% clouds gives offset = offset_min (30 min)."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 60
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        delta = raw - adjusted
        assert abs(delta.total_seconds() - 30 * 60) < 1, (
            f"Expected 30-min offset at threshold (60% clouds), "
            f"but got {delta.total_seconds() / 60:.2f} min; "
            "linear interpolation at t=0 must yield offset_min."
        )

    def test_offset_at_100_percent(self):
        """Adverse at 100% clouds gives offset = offset_max (75 min)."""
        data = _load_fixture("snow.json")
        data["clouds"]["all"] = 100
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        delta = raw - adjusted
        assert abs(delta.total_seconds() - 75 * 60) < 1, (
            f"Expected 75-min offset at 100% clouds, "
            f"but got {delta.total_seconds() / 60:.2f} min; "
            "linear interpolation at t=1 must yield offset_max."
        )

    def test_linear_interpolation_midpoint(self):
        """Adverse at 80% clouds (midpoint of 60 to 100) gives offset = 52.5 min."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 80
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        # t = (80-60)/(100-60) = 0.5 so offset = 30 + 45*0.5 = 52.5 min
        expected_seconds = 52.5 * 60
        delta = raw - adjusted
        assert abs(delta.total_seconds() - expected_seconds) < 1, (
            f"Expected 52.5-min offset at 80% clouds (midpoint), "
            f"but got {delta.total_seconds() / 60:.2f} min; "
            "linear interpolation at t=0.5 must yield 52.5 min."
        )

    def test_adverse_below_threshold_no_offset(self):
        """Rain (adverse) but only 50% clouds (below 60% threshold) gives no offset."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 50
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        assert adjusted == raw, (
            f"Expected no offset when adverse but clouds (50%) below threshold (60%), "
            f"but raw={raw} and adjusted={adjusted} differ; "
            "cloud_pct < cloud_threshold must bypass the offset calculation."
        )
