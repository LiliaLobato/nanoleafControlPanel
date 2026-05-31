"""nanoleafLight

Light wrapper around the Nanoleaf OpenAPI for the sunrise/sunset controller.
Handles all Nanoleaf HTTP calls, timeouts, and exception mapping.

Refer to the full nanoleafapi wrapper for discovery, setup, and advanced functions:
https://github.com/MylesMor/nanoleafapi
"""

import colorsys
import json
import logging
from typing import Any, Optional

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError, RequestException, Timeout

logger = logging.getLogger(__name__)

_JSON_HEADERS = {"Content-Type": "application/json"}


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

class NanoleafLight:
    def __init__(self, name: str, ip: str, auth_token: str = "", port: int = 16021):
        self.name = name
        self.ip = ip
        self.port = port
        self.auth_token = auth_token
        # _base_url omits the token so the attribute is safe to log.
        # The full URL (with token) is assembled inside _request only.
        self._base_url = f"http://{ip}:{port}/api/v1"

    def __str__(self) -> str:
        return f"{self.name}: {self.ip} - Auth setup: {bool(self.auth_token)}"

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Execute an HTTP request with timeouts and exception mapping.

        All public methods route through here so timeout and error handling
        are applied consistently. PUT requests automatically include
        Content-Type: application/json when a body is present.

        :raises NanoleafConnectionError: on network failure or timeout
        :raises NanoleafAuthError: on 401/403
        :raises NanoleafRequestError: on other non-2xx HTTP status
        """
        kwargs.setdefault("timeout", (3, 5))
        if "data" in kwargs:
            headers = dict(kwargs.get("headers", {}))
            headers.setdefault("Content-Type", "application/json")
            kwargs["headers"] = headers
        url = f"{self._base_url}/{self.auth_token}{path}"
        try:
            response = requests.request(method, url, **kwargs)
        except (RequestsConnectionError, Timeout) as exc:
            raise NanoleafConnectionError(
                f"Connection failed to {self.ip}:{self.port}"
            ) from exc
        except RequestException as exc:
            raise NanoleafConnectionError(
                f"Request failed to {self.ip}:{self.port}"
            ) from exc

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
        except NanoleafAuthError as exc:
            logger.warning("get_full_state: auth error (%s)", exc)
            return {}
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
        except NanoleafAuthError as exc:
            logger.warning("power_off: auth error (%s)", exc)
            return False
        except NanoleafError:
            return False

    def power_on(self) -> bool:
        """Power on the lights."""
        try:
            self._request("PUT", "/state", data=json.dumps({"on": {"value": True}}))
            return True
        except NanoleafAuthError as exc:
            logger.warning("power_on: auth error (%s)", exc)
            return False
        except NanoleafError:
            return False

    def get_power(self) -> bool:
        """Return True if the lights are on."""
        try:
            response = self._request("GET", "/state/on")
            return json.loads(response.text)["value"]
        except NanoleafError:
            return False
        except (KeyError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Batched colour setters
    # ------------------------------------------------------------------

    def set_hsb(
        self,
        hue: int,
        saturation: int,
        brightness: int,
        duration: int = 0,
        on: Optional[bool] = None,
    ) -> bool:
        """Set hue, saturation, and brightness in a single batched PUT /state call.

        :param hue: 0–359 (Nanoleaf API range; 360 is not a valid value)
        :param saturation: 0–100
        :param brightness: 0–100
        :param duration: transition duration in tenths of a second (0 = instant)
        :param on: if provided, include power state in the same call (True=on, False=off)
        :returns: True if successful, otherwise False
        """
        hue = max(0, min(hue, 359))  # clamp: colorsys round(h*360) can produce 360
        if not 0 <= saturation <= 100:
            raise ValueError("Saturation should be between 0 and 100")
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness should be between 0 and 100")
        data = {
            "hue": {"value": hue},
            "sat": {"value": saturation},
            "brightness": {"value": brightness, "duration": duration},
        }
        if on is not None:
            data["on"] = {"value": on}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def set_color_temp_and_brightness(
        self,
        ct: int,
        brightness: int,
        duration: int = 0,
        on: Optional[bool] = None,
    ) -> bool:
        """Set colour temperature and brightness in a single batched PUT /state call.

        :param ct: colour temperature in Kelvin (1200–6500)
        :param brightness: 0–100
        :param duration: transition duration in tenths of a second (0 = instant)
        :param on: if provided, include power state in the same call (True=on, False=off)
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
        if on is not None:
            data["on"] = {"value": on}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def restore_state(self, snapshot: dict) -> bool:
        """Restore lamp to a previously captured get_full_state() snapshot."""
        if not snapshot:
            return True
        on = snapshot.get("on", True)
        if snapshot.get("colorMode") == "ct":
            return self.set_color_temp_and_brightness(
                snapshot["ct"], snapshot["brightness"], on=on
            )
        return self.set_hsb(
            snapshot["hue"], snapshot["sat"], snapshot["brightness"], on=on
        )

    def set_color(
        self,
        rgb: tuple[int, int, int],
        on: Optional[bool] = None,
    ) -> bool:
        """Set the light colour from an RGB tuple via a batched /state call.

        Converts RGB (0–255 per channel) to HSB using colorsys, then sends
        a single batched PUT. Used by callers that work in RGB color space.

        :param rgb: (r, g, b) tuple, each channel 0–255
        :param on: if provided, include power state in the same call (True=on, False=off)
        :returns: True if successful, otherwise False
        """
        r, g, b = rgb
        if not all(0 <= c <= 255 for c in (r, g, b)):
            raise ValueError("RGB channels must each be between 0 and 255")
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        return self.set_hsb(round(h * 360), round(s * 100), round(v * 100), on=on)
