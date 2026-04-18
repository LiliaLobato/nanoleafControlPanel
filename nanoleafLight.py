"""nanoleafLight

Light wrapper around the Nanoleaf OpenAPI for the sunrise/sunset controller.
Handles all Nanoleaf HTTP calls, timeouts, and exception mapping.

Refer to the full nanoleafapi wrapper for discovery, setup, and advanced functions:
https://github.com/MylesMor/nanoleafapi
"""

import colorsys
import json
import requests
from requests.exceptions import ConnectionError, Timeout, RequestException
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Preset colours (RGB tuples)
# ---------------------------------------------------------------------------

RED = (255, 0, 0)
ORANGE = (255, 165, 0)
YELLOW = (255, 255, 0)
GREEN = (0, 255, 0)
LIGHT_BLUE = (173, 216, 230)
BLUE = (0, 0, 255)
PINK = (255, 192, 203)
PURPLE = (128, 0, 128)
WHITE = (255, 255, 255)


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
    def __init__(
        self,
        name: str,
        ip: str,
        auth_token: str = "",
        port: str = "16021",
        print_errors: bool = True,
        full_debug: bool = False,
    ):
        self.name = name
        self.ip = ip
        self.port = port
        self.auth_token = auth_token
        self.print_errors = print_errors
        self.full_debug = full_debug
        self.url = f"http://{ip}:{port}/api/v1/{auth_token}"

    # ------------------------------------------------------------------
    # Internal helpers
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
        except (ConnectionError, Timeout) as exc:
            raise NanoleafConnectionError(str(exc)) from exc
        except RequestException as exc:
            raise NanoleafConnectionError(str(exc)) from exc

        if response.status_code in (200, 204):
            return response

        if response.status_code in (401, 403):
            raise NanoleafAuthError(f"HTTP {response.status_code}: auth error")

        raise NanoleafRequestError(f"HTTP {response.status_code}")

    def __error_check(self, code: int) -> bool:
        """Return True for 200/204; print and return False for all other codes."""
        if code in (200, 204):
            if self.full_debug:
                print(f"{code}: Action performed successfully.")
            return True
        if self.print_errors:
            messages = {
                400: "Error 400: Bad request.",
                401: "Error 401: Unauthorized, invalid auth token. Please generate a new one.",
                403: "Error 403: Unauthorized, please hold the power button on the controller for 5-7 seconds, then try again.",
                404: "Error 404: Resource not found.",
                500: "Error 500: Internal server error.",
            }
            print(messages.get(code, f"Error {code}: Huh..."))
        return False

    def __str__(self) -> str:
        return f"{self.name}: {self.ip} - Auth setup: {self.isAuthTokenSetup()}"

    def isAuthTokenSetup(self) -> bool:
        """Return True if an auth token has been provided."""
        return bool(self.auth_token)

    # ------------------------------------------------------------------
    # Identify / info
    # ------------------------------------------------------------------

    def identify(self) -> bool:
        """Run the identify sequence on the lights."""
        try:
            self._request("PUT", "/identify")
            return True
        except NanoleafError:
            return False

    def check_heartbeat(self) -> bool:
        """Return True if the lamp is reachable and responding."""
        try:
            self._request("GET", "")
            return True
        except NanoleafError:
            return False

    def get_info(self) -> Dict[str, Any]:
        """Single GET to the device root. Returns full device info dict.

        :raises NanoleafConnectionError: on network failure
        :raises NanoleafAuthError: on auth failure
        :raises NanoleafRequestError: on HTTP error
        """
        response = self._request("GET", "")
        return json.loads(response.text)

    def get_full_state(self) -> Dict[str, Any]:
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
        except (KeyError, ValueError):
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

    def toggle_power(self) -> bool:
        """Toggle the lights on/off."""
        if self.get_power():
            return self.power_off()
        return self.power_on()

    # ------------------------------------------------------------------
    # Batched colour setters (Phase 2 additions)
    # ------------------------------------------------------------------

    def set_hsb(self, hue: int, saturation: int, brightness: int, duration: int = 0) -> bool:
        """Set hue, saturation, and brightness in a single batched PUT /state call.

        :param hue: 0–360
        :param saturation: 0–100
        :param brightness: 0–100
        :param duration: transition duration in tenths of a second (0 = instant)
        :returns: True if successful, otherwise False
        """
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
        data = {
            "ct": {"value": ct},
            "brightness": {"value": brightness, "duration": duration},
        }
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def set_color(self, rgb: Tuple[int, int, int]) -> bool:
        """Set the light colour from an RGB tuple via a batched /state call.

        Converts RGB (0–255 per channel) to HSB using colorsys, then sends
        a single batched PUT. Used primarily by party mode's --color option.

        :param rgb: (r, g, b) tuple, each channel 0–255
        :returns: True if successful, otherwise False
        """
        r, g, b = rgb
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        hue = round(h * 360)
        saturation = round(s * 100)
        brightness = round(v * 100)
        return self.set_hsb(hue, saturation, brightness)

    # ------------------------------------------------------------------
    # Individual setters (kept for backwards compatibility)
    # ------------------------------------------------------------------

    def set_brightness(self, brightness: int, duration: int = 0) -> bool:
        """Set the brightness of the lights (0–100)."""
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness should be between 0 and 100")
        data = {"brightness": {"value": brightness, "duration": duration}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def increment_brightness(self, brightness: int) -> bool:
        """Increment the brightness by the given amount (can be negative)."""
        data = {"brightness": {"increment": brightness}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def get_brightness(self) -> Optional[int]:
        """Return the current brightness value, or None on failure."""
        try:
            response = self._request("GET", "/state/brightness")
            return json.loads(response.text)["value"]
        except NanoleafError:
            return None

    def set_hue(self, value: int) -> bool:
        """Set the hue of the lights (0–360)."""
        if not 0 <= value <= 360:
            raise ValueError("Hue should be between 0 and 360")
        data = {"hue": {"value": value}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def increment_hue(self, value: int) -> bool:
        """Increment the hue by the given amount (can be negative)."""
        data = {"hue": {"increment": value}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def get_hue(self) -> Optional[int]:
        """Return the current hue value, or None on failure."""
        try:
            response = self._request("GET", "/state/hue")
            return json.loads(response.text)["value"]
        except NanoleafError:
            return None

    def set_saturation(self, value: int) -> bool:
        """Set the saturation of the lights (0–100)."""
        if not 0 <= value <= 100:
            raise ValueError("Saturation should be between 0 and 100")
        data = {"sat": {"value": value}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def increment_saturation(self, value: int) -> bool:
        """Increment the saturation by the given amount (can be negative)."""
        data = {"sat": {"increment": value}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def get_saturation(self) -> Optional[int]:
        """Return the current saturation value, or None on failure."""
        try:
            response = self._request("GET", "/state/sat")
            return json.loads(response.text)["value"]
        except NanoleafError:
            return None

    def set_color_temp(self, value: int) -> bool:
        """Set the white colour temperature (1200–6500 K)."""
        if not 1200 <= value <= 6500:
            raise ValueError("Colour temp should be between 1200 and 6500")
        data = {"ct": {"value": value}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def increment_color_temp(self, value: int) -> bool:
        """Increment the colour temperature by the given amount (can be negative)."""
        data = {"ct": {"increment": value}}
        try:
            self._request("PUT", "/state", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def get_color_temp(self) -> Optional[int]:
        """Return the current colour temperature, or None on failure."""
        try:
            response = self._request("GET", "/state/ct")
            return json.loads(response.text)["value"]
        except NanoleafError:
            return None

    def get_color_mode(self) -> Optional[str]:
        """Return the current colour mode ('ct' or 'hs'), or None on failure."""
        try:
            response = self._request("GET", "/state/colorMode")
            return json.loads(response.text)
        except NanoleafError:
            return None

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------

    def get_current_effect(self) -> Optional[str]:
        """Return the currently selected effect name, or None on failure."""
        try:
            response = self._request("GET", "/effects/select")
            return json.loads(response.text)
        except NanoleafError:
            return None

    def set_effect(self, effect_name: str) -> bool:
        """Set the active effect by name."""
        data = {"select": effect_name}
        try:
            self._request("PUT", "/effects", data=json.dumps(data))
            return True
        except NanoleafError:
            return False

    def list_effects(self) -> Optional[List[str]]:
        """Return a list of available effect names, or None on failure."""
        try:
            response = self._request("GET", "/effects/effectsList")
            return json.loads(response.text)
        except NanoleafError:
            return None
