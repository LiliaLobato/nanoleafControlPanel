"""Tests for OpenWeatherLight.from_cache() and the live-API constructor."""

from unittest.mock import MagicMock, patch

import pytest

from weather.openWeather import OpenWeatherLight
from tests.conftest import _load_fixture, LAT, LON


class TestFromCache:
    def test_no_network_call(self):
        """from_cache must NOT make any HTTP requests."""
        raw = _load_fixture("clear.json")
        with patch("weather.openWeather.requests.get") as mock_get:
            w = OpenWeatherLight.from_cache(raw, LAT, LON)
            mock_get.assert_not_called()
        assert w.is_valid is True, (
            "Expected is_valid to be True after from_cache(), "
            f"but got {w.is_valid}; _init_from_raw must set is_valid=True."
        )

    def test_equivalent_fields(self):
        """from_cache should produce the same fields as a live API call would."""
        raw = _load_fixture("rain.json")

        # Build from cache
        cached = OpenWeatherLight.from_cache(raw, LAT, LON)

        # Build from a mocked live call
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status = MagicMock()
        with patch("weather.openWeather.requests.get", return_value=mock_response):
            live = OpenWeatherLight(LAT, LON, auth_token="fake")

        assert cached.timestamp == live.timestamp, (
            f"Expected cached.timestamp == live.timestamp, "
            f"got cached={cached.timestamp}, live={live.timestamp}."
        )
        assert cached.name == live.name, (
            f"Expected cached.name == live.name, "
            f"got cached={cached.name!r}, live={live.name!r}."
        )
        assert cached.weather.condition_id == live.weather.condition_id, (
            f"Expected condition_id to match, "
            f"got cached={cached.weather.condition_id}, live={live.weather.condition_id}."
        )
        assert cached.weather.main == live.weather.main, (
            f"Expected weather.main to match, "
            f"got cached={cached.weather.main!r}, live={live.weather.main!r}."
        )
        assert cached.weather.clouds == live.weather.clouds, (
            f"Expected weather.clouds to match, "
            f"got cached={cached.weather.clouds}, live={live.weather.clouds}."
        )
        assert cached.weather.humidity == live.weather.humidity, (
            f"Expected weather.humidity to match, "
            f"got cached={cached.weather.humidity}, live={live.weather.humidity}."
        )
        assert cached.temperature.temperature == live.temperature.temperature, (
            f"Expected temperature to match, "
            f"got cached={cached.temperature.temperature}, live={live.temperature.temperature}."
        )
        assert cached.timezone.sunrise_ts == live.timezone.sunrise_ts, (
            f"Expected sunrise_ts to match, "
            f"got cached={cached.timezone.sunrise_ts}, live={live.timezone.sunrise_ts}."
        )
        assert cached.timezone.sunset_ts == live.timezone.sunset_ts, (
            f"Expected sunset_ts to match, "
            f"got cached={cached.timezone.sunset_ts}, live={live.timezone.sunset_ts}."
        )
        assert cached.has_adverse_conditions() == live.has_adverse_conditions(), (
            f"Expected has_adverse_conditions() to match, "
            f"got cached={cached.has_adverse_conditions()}, live={live.has_adverse_conditions()}."
        )
        assert cached.get_sunrise_dt() == live.get_sunrise_dt(), (
            f"Expected get_sunrise_dt() to match, "
            f"got cached={cached.get_sunrise_dt()}, live={live.get_sunrise_dt()}."
        )
        assert cached.get_sunset_dt() == live.get_sunset_dt(), (
            f"Expected get_sunset_dt() to match, "
            f"got cached={cached.get_sunset_dt()}, live={live.get_sunset_dt()}."
        )

    def test_raw_data_preserved(self):
        """from_cache should store the raw_data for re-caching."""
        raw = _load_fixture("fog.json")
        w = OpenWeatherLight.from_cache(raw, LAT, LON)
        assert w.raw_data == raw, (
            "Expected w.raw_data to equal the input raw dict, "
            f"but they differ; _init_from_raw must assign self.raw_data = raw_data."
        )

    def test_coordinates_stored(self):
        """from_cache should store lat/lon correctly."""
        raw = _load_fixture("clear.json")
        w = OpenWeatherLight.from_cache(raw, LAT, LON)
        assert w.latitude == LAT, (
            f"Expected w.latitude == {LAT}, got {w.latitude}; "
            "from_cache must assign instance.latitude = float(latitude)."
        )
        assert w.longitude == LON, (
            f"Expected w.longitude == {LON}, got {w.longitude}; "
            "from_cache must assign instance.longitude = float(longitude)."
        )


class TestConstructor:
    def test_live_api_mocked(self):
        """Constructor makes an API call and populates all fields."""
        raw = _load_fixture("clear.json")
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status = MagicMock()
        with patch("weather.openWeather.requests.get", return_value=mock_response) as mock_get:
            w = OpenWeatherLight(LAT, LON, auth_token="test_token")
            mock_get.assert_called_once()

        assert w.is_valid is True, (
            f"Expected is_valid to be True after construction, got {w.is_valid}."
        )
        assert w.name == "Test Location", (
            f"Expected w.name == 'Test Location', got {w.name!r}; "
            "fixture name field must match updated fixture value."
        )
        assert w.weather.main == "Clear", (
            f"Expected w.weather.main == 'Clear', got {w.weather.main!r}; "
            "weather.main must be populated from the raw response."
        )
        assert w.raw_data == raw, (
            "Expected w.raw_data to equal the mock response dict, "
            "but they differ; raw_data must be stored as-is."
        )

    def test_timeout_in_request(self):
        """Constructor should pass timeout=(3, 5) to requests.get."""
        raw = _load_fixture("clear.json")
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status = MagicMock()
        with patch("weather.openWeather.requests.get", return_value=mock_response) as mock_get:
            OpenWeatherLight(LAT, LON, auth_token="test")
            call_kwargs = mock_get.call_args
            assert call_kwargs.kwargs["timeout"] == (3, 5), (
                f"Expected requests.get to be called with timeout=(3, 5), "
                f"got timeout={call_kwargs.kwargs.get('timeout')}; "
                "the _fetch method must pass this exact timeout tuple."
            )
