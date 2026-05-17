"""openWeather.py

OpenWeather API wrapper for the Nanoleaf Sunrise/Sunset Controller.

Provides weather data, adverse condition detection, sun position calculation,
and darkness evaluation. Designed to be imported without side effects;
all test/demo code is behind if __name__ == "__main__".
"""

import json
import os
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

from weather.noaa_solar import get_sun_elevation as _noaa_elevation


# Condition codes that indicate reduced outdoor light
class WeatherFetchError(Exception):
    """Network or HTTP failure when calling the OpenWeather API."""


_ADVERSE_CODE_RANGES = [
    (200, 299),  # Thunderstorm
    (300, 399),  # Drizzle
    (500, 599),  # Rain
    (600, 699),  # Snow
    (700, 799),  # Atmosphere (fog, mist, haze, smoke, etc.)
]
_ADVERSE_CLOUD_CODES = {803, 804}  # broken clouds, overcast


class Temperature:
    def __init__(self, raw_data: dict):
        self.temperature = raw_data["main"]["temp"]
        self.feels_like = raw_data["main"]["feels_like"]
        self.temp_min = raw_data["main"]["temp_min"]
        self.temp_max = raw_data["main"]["temp_max"]


class Timezone:
    def __init__(self, raw_data: dict):
        self.utc_offset = raw_data["timezone"]
        self.sunrise_ts = raw_data["sys"]["sunrise"]
        self.sunset_ts = raw_data["sys"]["sunset"]


class Weather:
    def __init__(self, raw_data: dict):
        self.condition_id = raw_data["weather"][0]["id"]
        self.main = raw_data["weather"][0]["main"]
        self.description = raw_data["weather"][0]["description"]
        self.humidity = raw_data["main"]["humidity"]
        self.clouds = raw_data["clouds"]["all"]


class OpenWeatherLight:
    """Wrapper around the OpenWeather Current Weather API.

    Construct via __init__ (live API call) or from_cache (cached raw JSON).
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        auth_token: str = "",
        units: str = "metric",
    ):
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.auth_token = auth_token
        self.units = units
        self.url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={latitude}&lon={longitude}&units={units}&appid={auth_token}"
        )

        raw_data = self._fetch()
        self._init_from_raw(raw_data)

    def _fetch(self) -> Dict[str, Any]:
        """Make the live API call and return raw JSON.

        :raises WeatherFetchError: on any network or HTTP failure
        """
        try:
            response = requests.get(self.url, timeout=(3, 5))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            raise WeatherFetchError(str(exc)) from exc

    def _init_from_raw(self, raw_data: Dict[str, Any]) -> None:
        """Populate all fields from a raw API response dict."""
        self.raw_data = raw_data
        self.is_valid = True
        self.timestamp = raw_data["dt"]
        self.name = raw_data.get("name", "")
        self.temperature = Temperature(raw_data)
        self.timezone = Timezone(raw_data)
        self.weather = Weather(raw_data)

    @classmethod
    def from_cache(
        cls,
        raw_data: dict,
        latitude: float,
        longitude: float,
    ) -> "OpenWeatherLight":
        """Reconstruct from cached raw API JSON without making a network call."""
        instance = cls.__new__(cls)
        instance.latitude = float(latitude)
        instance.longitude = float(longitude)
        instance.auth_token = ""
        instance.units = ""
        instance.url = ""
        instance._init_from_raw(raw_data)
        return instance

    # --- Adverse conditions ---

    def has_adverse_conditions(self) -> bool:
        """True if weather condition code implies reduced outdoor light.

        Triggers on: Thunderstorm (2xx), Drizzle (3xx), Rain (5xx), Snow (6xx),
        Atmosphere/fog/mist/haze (7xx), broken/overcast clouds (803 to 804).
        Does NOT trigger on: Clear (800), few/scattered clouds (801 to 802).
        """
        code = self.weather.condition_id
        for low, high in _ADVERSE_CODE_RANGES:
            if low <= code <= high:
                return True
        return code in _ADVERSE_CLOUD_CODES

    # --- Sunrise / sunset ---

    def get_sunrise_dt(self, tz: dt_timezone = dt_timezone.utc) -> datetime:
        """Sunrise as a tz-aware datetime in the given timezone."""
        return datetime.fromtimestamp(self.timezone.sunrise_ts, tz=dt_timezone.utc).astimezone(tz)

    def get_sunset_dt(self, tz: dt_timezone = dt_timezone.utc) -> datetime:
        """Sunset as a tz-aware datetime in the given timezone."""
        return datetime.fromtimestamp(self.timezone.sunset_ts, tz=dt_timezone.utc).astimezone(tz)

    def get_adjusted_sunset(
        self,
        cloud_threshold: int = 60,
        offset_min: int = 30,
        offset_max: int = 75,
        tz: dt_timezone = dt_timezone.utc,
    ) -> datetime:
        """Sunset adjusted earlier based on cloud cover %.

        Linear interpolation from offset_min (at threshold) to offset_max (at 100%).
        Returns raw sunset if cloud cover < threshold or no adverse conditions.
        """
        sunset = self.get_sunset_dt(tz=tz)
        cloud_pct = self.weather.clouds

        if not self.has_adverse_conditions() or cloud_pct < cloud_threshold:
            return sunset

        # Linear interpolation: threshold% to offset_min, 100% to offset_max
        t = (cloud_pct - cloud_threshold) / (100 - cloud_threshold)
        offset_minutes = offset_min + (offset_max - offset_min) * t
        return sunset - timedelta(minutes=offset_minutes)

    # --- Sun elevation (NOAA formula) ---

    def get_sun_elevation(self, at: Optional[datetime] = None) -> float:
        """Sun elevation angle in degrees at the given time (or now).

        Pure math from lat/lon/UTC datetime, no API call.
        Uses the NOAA solar position algorithm.
        """
        return _noaa_elevation(self.latitude, self.longitude, at=at)

    # --- Darkness detection ---

    def is_dark_outside(
        self,
        dark_elevation_deg: float = 20.0,
        dark_cloud_threshold: int = 75,
        at: Optional[datetime] = None,
    ) -> bool:
        """True if conditions indicate darkness outside.

        Dark if:
        - Sun elevation < 0 (below horizon), OR
        - Sun elevation < dark_elevation_deg AND adverse conditions AND clouds > dark_cloud_threshold
        """
        elevation = self.get_sun_elevation(at=at)

        if elevation < 0:
            return True

        if elevation < dark_elevation_deg and self.has_adverse_conditions() and self.weather.clouds > dark_cloud_threshold:
            return True

        return False


# --- Module entry point (demo / manual testing only) ---

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(script_dir, ".env"))

    LATITUDE = os.getenv("OPENWEATHER_LATITUDE")
    LONGITUDE = os.getenv("OPENWEATHER_LONGITUDE")
    AUTH_TOKEN = os.getenv("OPENWEATHER_AUTH_TOKEN")

    my_weather = OpenWeatherLight(LATITUDE, LONGITUDE, AUTH_TOKEN)

    print(f"Location: {my_weather.name}")
    print(f"Weather: {my_weather.weather.main}, {my_weather.weather.description}")
    print(f"Condition ID: {my_weather.weather.condition_id}")
    print(f"Clouds: {my_weather.weather.clouds}%")
    print(f"Adverse conditions: {my_weather.has_adverse_conditions()}")
    print(f"Sunrise: {my_weather.get_sunrise_dt()}")
    print(f"Sunset: {my_weather.get_sunset_dt()}")
    print(f"Adjusted sunset: {my_weather.get_adjusted_sunset()}")
    print(f"Sun elevation: {my_weather.get_sun_elevation():.1f} deg")
    print(f"Dark outside: {my_weather.is_dark_outside()}")
