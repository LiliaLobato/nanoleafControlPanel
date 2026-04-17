"""Unit tests for openWeather.py — Phase 1 of Nanoleaf Sunset Controller."""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

from openWeather import OpenWeatherLight, _julian_date, LOCAL_TZ

FIXTURES = Path(__file__).parent / "fixtures"
LAT = 47.6144
LON = -122.1923


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_instance(fixture_name: str) -> OpenWeatherLight:
    """Build an OpenWeatherLight from a fixture via from_cache (no network)."""
    return OpenWeatherLight.from_cache(_load_fixture(fixture_name), LAT, LON)


# ── Fixtures (pytest) ──────────────────────────────────────────────────────

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


# ── has_adverse_conditions ─────────────────────────────────────────────────

class TestHasAdverseConditions:
    @pytest.mark.parametrize("fixture_name, expected", [
        ("clear.json", False),           # 800 — Clear
        ("partly_cloudy.json", False),   # 802 — scattered clouds
        ("rain.json", True),             # 501 — moderate rain
        ("snow.json", True),             # 601 — snow
        ("fog.json", True),              # 741 — fog
        ("overcast.json", True),         # 804 — overcast clouds
        ("thunderstorm.json", True),     # 211 — thunderstorm
    ])
    def test_condition_codes(self, fixture_name, expected):
        w = _make_instance(fixture_name)
        assert w.has_adverse_conditions() is expected

    def test_few_clouds_not_adverse(self):
        """Code 801 (few clouds) should NOT be adverse."""
        data = _load_fixture("clear.json")
        data["weather"][0]["id"] = 801
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        assert w.has_adverse_conditions() is False

    def test_broken_clouds_adverse(self):
        """Code 803 (broken clouds) SHOULD be adverse."""
        data = _load_fixture("clear.json")
        data["weather"][0]["id"] = 803
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        assert w.has_adverse_conditions() is True

    def test_drizzle_adverse(self):
        """Code 300 (drizzle) should be adverse."""
        data = _load_fixture("clear.json")
        data["weather"][0]["id"] = 300
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        assert w.has_adverse_conditions() is True


# ── Sunrise / Sunset datetimes ─────────────────────────────────────────────

class TestSunriseSunset:
    """All fixtures share the same timestamps: sunrise 1750504260, sunset 1750561860."""

    def test_sunrise_is_tz_aware(self, clear):
        dt = clear.get_sunrise_dt()
        assert dt.tzinfo is not None

    def test_sunset_is_tz_aware(self, clear):
        dt = clear.get_sunset_dt()
        assert dt.tzinfo is not None

    def test_sunrise_correct_local_time(self, clear):
        """1750504260 → 2025-06-21 04:11:00 PDT."""
        dt = clear.get_sunrise_dt()
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.day == 21
        assert dt.hour == 4
        assert dt.minute == 11

    def test_sunset_correct_local_time(self, clear):
        """1750561860 → 2025-06-21 20:11:00 PDT."""
        dt = clear.get_sunset_dt()
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.day == 21
        assert dt.hour == 20
        assert dt.minute == 11

    def test_sunrise_before_sunset(self, clear):
        assert clear.get_sunrise_dt() < clear.get_sunset_dt()


# ── Adjusted sunset ────────────────────────────────────────────────────────

class TestAdjustedSunset:
    def test_no_offset_below_threshold(self, clear):
        """Clear sky (5% clouds): no adjustment regardless of thresholds."""
        raw = clear.get_sunset_dt()
        adjusted = clear.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        assert adjusted == raw

    def test_no_offset_non_adverse_above_threshold(self, partly_cloudy):
        """Partly cloudy (802, 40% clouds): not adverse → no adjustment."""
        raw = partly_cloudy.get_sunset_dt()
        adjusted = partly_cloudy.get_adjusted_sunset(cloud_threshold=30, offset_min=30, offset_max=75)
        assert adjusted == raw

    def test_offset_at_threshold(self):
        """Adverse at exactly threshold% → offset = offset_min (30 min)."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 60
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        delta = raw - adjusted
        assert abs(delta.total_seconds() - 30 * 60) < 1

    def test_offset_at_100_percent(self):
        """Adverse at 100% clouds → offset = offset_max (75 min)."""
        data = _load_fixture("snow.json")
        data["clouds"]["all"] = 100
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        delta = raw - adjusted
        assert abs(delta.total_seconds() - 75 * 60) < 1

    def test_linear_interpolation_midpoint(self):
        """Adverse at 80% clouds (midpoint of 60-100) → offset = 52.5 min."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 80
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        # t = (80-60)/(100-60) = 0.5 → offset = 30 + 45*0.5 = 52.5 min
        expected_seconds = 52.5 * 60
        delta = raw - adjusted
        assert abs(delta.total_seconds() - expected_seconds) < 1

    def test_adverse_below_threshold_no_offset(self):
        """Rain (adverse) but only 50% clouds (below 60% threshold) → no offset."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 50
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        raw = w.get_sunset_dt()
        adjusted = w.get_adjusted_sunset(cloud_threshold=60, offset_min=30, offset_max=75)
        assert adjusted == raw


# ── Sun elevation (NOAA cross-check) ──────────────────────────────────────

class TestSunElevation:
    """Cross-check against known NOAA values for Bellevue, WA (47.6144, -122.1923).

    Tolerance: ±0.5° as specified in the plan.
    Reference values computed with the same NOAA formula for consistency.
    """

    def _make_weather(self):
        return _make_instance("clear.json")

    @pytest.mark.parametrize("utc_dt, expected_elevation", [
        # Summer solstice noon: Jun 21 2025 12:00 PDT = 19:00 UTC
        (datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc), 62.02),
        # Winter solstice noon: Dec 21 2025 12:00 PST = 20:00 UTC
        (datetime(2025, 12, 21, 20, 0, 0, tzinfo=timezone.utc), 18.93),
        # Equinox dawn: Mar 20 2025 06:30 PDT = 13:30 UTC (below horizon)
        (datetime(2025, 3, 20, 13, 30, 0, tzinfo=timezone.utc), -7.69),
        # Equinox dusk: Sep 22 2025 19:00 PDT = Sep 23 02:00 UTC (near horizon)
        (datetime(2025, 9, 23, 2, 0, 0, tzinfo=timezone.utc), 0.11),
    ])
    def test_known_elevations(self, utc_dt, expected_elevation):
        w = self._make_weather()
        elevation = w.get_sun_elevation(at=utc_dt)
        assert abs(elevation - expected_elevation) < 0.5, (
            f"Expected ~{expected_elevation}°, got {elevation:.2f}°"
        )

    def test_summer_noon_is_highest(self):
        """Summer noon should have highest elevation of the four reference points."""
        w = self._make_weather()
        summer_noon = datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc)
        elev = w.get_sun_elevation(at=summer_noon)
        assert elev > 60

    def test_below_horizon_negative(self):
        """Before sunrise, elevation should be negative."""
        w = self._make_weather()
        pre_dawn = datetime(2025, 3, 20, 13, 30, 0, tzinfo=timezone.utc)
        assert w.get_sun_elevation(at=pre_dawn) < 0

    def test_rejects_naive_datetime(self):
        """Must raise ValueError for naive (non-tz-aware) datetime."""
        w = self._make_weather()
        with pytest.raises(ValueError, match="timezone-aware"):
            w.get_sun_elevation(at=datetime(2025, 6, 21, 12, 0, 0))

    def test_defaults_to_now(self):
        """Calling with no argument should return a float (uses current time)."""
        w = self._make_weather()
        elev = w.get_sun_elevation()
        assert isinstance(elev, float)
        assert -90 <= elev <= 90


# ── is_dark_outside ───────────────────────────────────────────────────────

class TestIsDarkOutside:
    """Truth table: (sun position) x (weather conditions) → dark or not."""

    def test_high_sun_clear_not_dark(self, clear):
        """High sun + clear sky → NOT dark."""
        # Summer noon: ~62° elevation, clear sky 5% clouds
        noon = datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc)
        assert clear.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=noon) is False

    def test_below_horizon_always_dark(self, clear):
        """Sun below horizon → dark regardless of weather."""
        pre_dawn = datetime(2025, 3, 20, 13, 30, 0, tzinfo=timezone.utc)
        assert clear.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=pre_dawn) is True

    def test_high_sun_overcast_not_dark(self, overcast):
        """High sun + overcast → NOT dark (elevation 65° >> threshold 20°)."""
        noon = datetime(2025, 6, 21, 19, 0, 0, tzinfo=timezone.utc)
        # Even though overcast, sun is at 62° which is well above threshold
        assert overcast.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=noon) is False

    def test_low_sun_overcast_is_dark(self):
        """Low sun (but above horizon) + overcast + high clouds → dark."""
        # Create a scenario: sun at ~19° (winter noon), overcast 95% clouds
        data = _load_fixture("overcast.json")
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        # Winter noon: elevation ~19° (below 20° threshold)
        winter_noon = datetime(2025, 12, 21, 20, 0, 0, tzinfo=timezone.utc)
        # overcast (804) is adverse, clouds=95% > 75% threshold, elevation ~19° < 20°
        assert w.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=winter_noon) is True

    def test_low_sun_clear_not_dark(self, clear):
        """Low sun + clear sky → NOT dark (no adverse conditions)."""
        winter_noon = datetime(2025, 12, 21, 20, 0, 0, tzinfo=timezone.utc)
        # clear (800) is NOT adverse, so even though sun is low, not dark
        assert clear.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=winter_noon) is False

    def test_low_sun_adverse_but_low_clouds_not_dark(self):
        """Low sun + adverse + clouds below threshold → NOT dark."""
        data = _load_fixture("rain.json")
        data["clouds"]["all"] = 50  # below 75% threshold
        w = OpenWeatherLight.from_cache(data, LAT, LON)
        winter_noon = datetime(2025, 12, 21, 20, 0, 0, tzinfo=timezone.utc)
        assert w.is_dark_outside(dark_elevation_deg=20.0, dark_cloud_threshold=75, at=winter_noon) is False


# ── from_cache ────────────────────────────────────────────────────────────

class TestFromCache:
    def test_no_network_call(self):
        """from_cache must NOT make any HTTP requests."""
        raw = _load_fixture("clear.json")
        with patch("openWeather.requests.get") as mock_get:
            w = OpenWeatherLight.from_cache(raw, LAT, LON)
            mock_get.assert_not_called()
        assert w.is_valid is True

    def test_equivalent_fields(self):
        """from_cache should produce the same fields as a live API call."""
        raw = _load_fixture("rain.json")

        # Build from cache
        cached = OpenWeatherLight.from_cache(raw, LAT, LON)

        # Build from "live" by mocking the network call
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status = MagicMock()
        with patch("openWeather.requests.get", return_value=mock_response):
            live = OpenWeatherLight(LAT, LON, auth_token="fake")

        # Compare all important fields
        assert cached.timestamp == live.timestamp
        assert cached.name == live.name
        assert cached.weather.condition_id == live.weather.condition_id
        assert cached.weather.main == live.weather.main
        assert cached.weather.clouds == live.weather.clouds
        assert cached.weather.humidity == live.weather.humidity
        assert cached.temperature.temperature == live.temperature.temperature
        assert cached.timezone.sunrise_ts == live.timezone.sunrise_ts
        assert cached.timezone.sunset_ts == live.timezone.sunset_ts
        assert cached.has_adverse_conditions() == live.has_adverse_conditions()
        assert cached.get_sunrise_dt() == live.get_sunrise_dt()
        assert cached.get_sunset_dt() == live.get_sunset_dt()

    def test_raw_data_preserved(self):
        """from_cache should store the raw_data for re-caching."""
        raw = _load_fixture("fog.json")
        w = OpenWeatherLight.from_cache(raw, LAT, LON)
        assert w.raw_data == raw

    def test_coordinates_stored(self):
        """from_cache should store lat/lon correctly."""
        raw = _load_fixture("clear.json")
        w = OpenWeatherLight.from_cache(raw, LAT, LON)
        assert w.latitude == LAT
        assert w.longitude == LON


# ── Julian date helper ─────────────────────────────────────────────────────

class TestJulianDate:
    def test_j2000_epoch(self):
        """J2000.0 epoch (2000-01-01 12:00 UTC) should be JD 2451545.0."""
        j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert abs(_julian_date(j2000) - 2451545.0) < 0.0001

    def test_known_date(self):
        """2025-06-21 00:00 UTC should be a known JD value."""
        dt = datetime(2025, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
        jd = _julian_date(dt)
        # JD for 2025-06-21 00:00 UTC ≈ 2460847.5
        assert abs(jd - 2460847.5) < 0.01


# ── Constructor / live API ────────────────────────────────────────────────

class TestConstructor:
    def test_live_api_mocked(self):
        """Constructor makes an API call and populates all fields."""
        raw = _load_fixture("clear.json")
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status = MagicMock()
        with patch("openWeather.requests.get", return_value=mock_response) as mock_get:
            w = OpenWeatherLight(LAT, LON, auth_token="test_token")
            mock_get.assert_called_once()

        assert w.is_valid is True
        assert w.name == "Bellevue"
        assert w.weather.main == "Clear"
        assert w.raw_data == raw

    def test_timeout_in_request(self):
        """Constructor should pass timeout=(3, 5) to requests.get."""
        raw = _load_fixture("clear.json")
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status = MagicMock()
        with patch("openWeather.requests.get", return_value=mock_response) as mock_get:
            OpenWeatherLight(LAT, LON, auth_token="test")
            call_kwargs = mock_get.call_args
            assert call_kwargs.kwargs["timeout"] == (3, 5)
