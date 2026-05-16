"""nanoleafLight

Light wrapper around the Nanoleaf OpenAPI for the sunrise/sunset controller.
Handles all Nanoleaf HTTP calls, timeouts, and exception mapping.

Refer to the full nanoleafapi wrapper for discovery, setup, and advanced functions:
https://github.com/MylesMor/nanoleafapi
"""

import colorsys
import json
import logging

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError, RequestException, Timeout
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class NanoleafError(Exception):
    """Base class for all Nanoleaf errors."""


class NanoleafConnectionError(NanoleafError):
    """Network failure: timeout, connection refused, host unreachable."""


class NanoleafAuthError(NanoleafError):
    """401 or 403: invalid or missing auth token."""


class NanoleafRequestError(NanoleafError):
    """400, 404, 500, or other HTTP error."""


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class nanoleafLight:
    def __init__(self, name: str, ip: str, auth_token: str = "", port: str = "16021"):
        self.name = name
        self.ip = ip
        self.port = port
        self.auth_token = auth_token
        self.url = f"http://{ip}:{port}/api/v1/{auth_token}"

    def __str__(self) -> str:
        return f"{self.name}: {self.ip} - Auth setup: {self.isAuthTokenSetup()}"

    def isAuthTokenSetup(self) -> bool:
        """Return True if an auth token has been provided."""
        return bool(self.auth_token)

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Execute an HTTP request with timeouts and exception mapping.

        All public methods route through here so timeout and error handling
        are applied consistently.

        :raises NanoleafConnectionError: on network failure or timeout
        :raises NanoleafAuthError: on 401/403
        :raises NanoleafRequestError: on other non-2xx HTTP status
        """
        kwargs.setdefault("timeout", (3, 5))
        url = self.url + path
        try:
            response = requests.request(method, url, **kwargs)
        except (RequestsConnectionError, Timeout) as exc:
            raise NanoleafConnectionError(str(exc)) from exc
        except RequestException as exc:
            raise NanoleafConnectionError(str(exc)) from exc

        if response.status_code in (200, 204):
            return response
        if response.status_code in (401, 403):
            raise NanoleafAuthError(f"HTTP {response.status_code}: auth error")
        raise NanoleafRequestError(f"HTTP {response.status_code}")

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def check_heartbeat(self) -> bool:
        """Return True if the lamp is reachable and responding."""
        try:
            self._request("GET", "")
            return True
        except NanoleafError:
            return False

    def get_info(self) -> dict[str, Any]:
        """Single GET to the device root. Returns full device info dict.

        :raises NanoleafConnectionError: on network failure
        :raises NanoleafAuthError: on auth failure
        :raises NanoleafRequestError: on HTTP error
        """
        response = self._request("GET", "")
        return json.loads(response.text)

    def get_full_state(self) -> dict[str, Any]:
        """Return the lamp's current state using a single round-trip.

        Extracts the 'state' subfield from get_info() and returns a flat dict:
        {on, hue, sat, brightness, ct, colorMode}

        :returns: dict with current state values, or {} on failure
        """
        try:
            info = self.get_info()
            state = info["state"]
            return {
                "on": state["on"]["value"],
                "hue": state["hue"]["value"],
                "sat": state["sat"]["value"],
                "brightness": state["brightness"]["value"],
                "ct": state["ct"]["value"],
                "colorMode": state["colorMode"],
            }
        except NanoleafError:
            return {}
        except (KeyError, ValueError) as exc:
            logger.warning("get_full_state: unexpected API response shape: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Power
    # ------------------------------------------------------------------

    def power_off(self) -> bool:
        """Power off the lights."""
        try:
            self._request("PUT", "/state", data=json.dumps({"on": {"value": False}}))
            return True
        except NanoleafError:
            return False

    def power_on(self) -> bool:
        """Power on the lights."""
        try:
            self._request("PUT", "/state", data=json.dumps({"on": {"value": True}}))
            return True
        except NanoleafError:
            return False

    def get_power(self) -> bool:
        """Return True if the lights are on."""
        try:
            response = self._request("GET", "/state/on")
            return json.loads(response.text)["value"]
        except NanoleafError:
            return False

    # ------------------------------------------------------------------
    # Batched colour setters
    # ------------------------------------------------------------------

    def set_hsb(self, hue: int, saturation: int, brightness: int, duration: int = 0) -> bool:
        """Set hue, saturation, and brightness in a single batched PUT /state call.

        :param hue: 0–360
        :param saturation: 0–100
        :param brightness: 0–100
        :param duration: transition duration in tenths of a second (0 = instant)
        :returns: True if successful, otherwise False
        """
        if not 0 <= hue <= 360:
            raise ValueError("Hue should be between 0 and 360")
        if not 0 <= saturation <= 100:
            raise ValueError("Saturation should be between 0 and 100")
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness should be between 0 and 100")
        data = {
            "hue": {"value": hue},
            "sat": {"value": saturation},
            "brightness": {"value": brightness, "duration": duration},
        }
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def set_color_temp_and_brightness(self, ct: int, brightness: int, duration: int = 0) -> bool:
        """Set colour temperature and brightness in a single batched PUT /state call.

        :param ct: colour temperature in Kelvin (1200–6500)
        :param brightness: 0–100
        :param duration: transition duration in tenths of a second (0 = instant)
        :returns: True if successful, otherwise False
        """
        if not 1200 <= ct <= 6500:
            raise ValueError("Colour temp should be between 1200 and 6500")
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness should be between 0 and 100")
        data = {
            "ct": {"value": ct},
            "brightness": {"value": brightness, "duration": duration},
        }
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def set_color(self, rgb: tuple[int, int, int]) -> bool:
        """Set the light colour from an RGB tuple via a batched /state call.

        Converts RGB (0–255 per channel) to HSB using colorsys, then sends
        a single batched PUT. Used primarily by party mode's --color option.

        :param rgb: (r, g, b) tuple, each channel 0–255
        :returns: True if successful, otherwise False
        """
        r, g, b = rgb
        if not all(0 <= c <= 255 for c in (r, g, b)):
            raise ValueError("RGB channels must each be between 0 and 255")
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return self.set_hsb(round(h * 360), round(s * 100), round(v * 100))
