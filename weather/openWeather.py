"""openWeather.py

OpenWeather API wrapper for the Nanoleaf Sunrise/Sunset Controller.

Provides weather data, adverse condition detection, sun position calculation,
and darkness evaluation. Designed to be imported without side effects;
all test/demo code is behind if __name__ == "__main__".
"""

import os
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Optional

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


class Timezone:
    def __init__(self, raw_data: dict):
        self.utc_offset = raw_data["timezone"]
        self.sunrise_ts = raw_data["sys"]["sunrise"]
        self.sunset_ts = raw_data["sys"]["sunset"]


class Weather:
    def __init__(self, raw_data: dict):
        self.condition_id = raw_data["weather"][0]["id"]
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

        raw_data = self._fetch()
        self._init_from_raw(raw_data)

    def _fetch(self) -> dict[str, Any]:
        """Make the live API call and return raw JSON.

        The auth token is kept out of the exception message so it does not
        appear in log files.

        :raises WeatherFetchError: on any network or HTTP failure
        """
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={self.latitude}&lon={self.longitude}"
            f"&units={self.units}&appid={self.auth_token}"
        )
        try:
            response = requests.get(url, timeout=(3, 5))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            raise WeatherFetchError(
                f"Weather API request failed (lat={self.latitude}, lon={self.longitude})"
            ) from exc

    def _init_from_raw(self, raw_data: dict[str, Any]) -> None:
        """Populate all fields from a raw API response dict.

        :raises WeatherFetchError: if raw_data is missing expected keys
        """
        try:
            self.raw_data = raw_data
            self.is_valid = True
            self.timestamp = raw_data["dt"]
            self.name = raw_data.get("name", "")
            self.timezone = Timezone(raw_data)
            self.weather = Weather(raw_data)
        except KeyError as exc:
            raise WeatherFetchError(
                f"Weather data missing expected field: {exc}"
            ) from exc

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
        instance.units = "metric"
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
        When cloud_threshold >= 100 the full offset_max is applied immediately.
        """
        sunset = self.get_sunset_dt(tz=tz)
        cloud_pct = self.weather.clouds

        if not self.has_adverse_conditions() or cloud_pct < cloud_threshold:
            return sunset

        if cloud_threshold >= 100:
            return sunset - timedelta(minutes=offset_max)

        # Linear interpolation: threshold% → offset_min, 100% → offset_max.
        # Clamp t to 1.0 in case cloud_pct > 100 (malformed API response).
        t = min((cloud_pct - cloud_threshold) / (100 - cloud_threshold), 1.0)
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
    load_dotenv()  # searches upward from CWD, finds project-root .env

    LATITUDE = os.getenv("OPENWEATHER_LATITUDE")
    LONGITUDE = os.getenv("OPENWEATHER_LONGITUDE")
    AUTH_TOKEN = os.getenv("OPENWEATHER_AUTH_TOKEN")

    my_weather = OpenWeatherLight(LATITUDE, LONGITUDE, AUTH_TOKEN)

    print(f"Location: {my_weather.name}")
    print(f"Condition ID: {my_weather.weather.condition_id}")
    print(f"Clouds: {my_weather.weather.clouds}%")
    print(f"Adverse conditions: {my_weather.has_adverse_conditions()}")
    print(f"Sunrise: {my_weather.get_sunrise_dt()}")
    print(f"Sunset: {my_weather.get_sunset_dt()}")
    print(f"Adjusted sunset: {my_weather.get_adjusted_sunset()}")
    print(f"Sun elevation: {my_weather.get_sun_elevation():.1f} deg")
    print(f"Dark outside: {my_weather.is_dark_outside()}")
