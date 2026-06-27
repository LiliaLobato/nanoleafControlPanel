"""Shared fixtures and helpers for the controller test suite."""

import json
from pathlib import Path

import pytest

from weather.openWeather import OpenWeatherLight

FIXTURES = Path(__file__).parent / "fixtures"

# NOAA math coordinates for Bellevue, WA; separate from fixture coord data.
LAT = 47.6144
LON = -122.1923


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


@pytest.fixture
def panel_ids_51():
    """Real-shaped 51-panel layout: ids 1-52 minus the Rhythm module (id 6).

    Matches the production lamp so K-count and even-spacing exercise realistic
    ordering rather than a tiny stub.
    """
    return [i for i in range(1, 53) if i != 6]
