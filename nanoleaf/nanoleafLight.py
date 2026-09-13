"""nanoleafLight

Light wrapper around the Nanoleaf OpenAPI for the sunrise/sunset controller.
Handles all Nanoleaf HTTP calls, timeouts, and exception mapping.

Refer to the full nanoleafapi wrapper for discovery, setup, and advanced functions:
https://github.com/MylesMor/nanoleafapi
"""

import json
import logging
import time
from typing import Any, Optional

from nanoleaf.color_helper import rgb_to_hsb

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
        except Timeout as exc:
            raise NanoleafConnectionError(
                f"Timeout connecting to {self.ip}:{self.port}"
            ) from exc
        except RequestsConnectionError as exc:
            raise NanoleafConnectionError(
                f"Connection refused by {self.ip}:{self.port}"
            ) from exc
        except RequestException as exc:
            raise NanoleafConnectionError(
                f"Request failed to {self.ip}:{self.port}"
            ) from exc

        if response.status_code in (200, 204):
            return response
        if response.status_code in (401, 403):
            raise NanoleafAuthError(f"HTTP {response.status_code}: auth error")
        raise NanoleafRequestError(f"HTTP {response.status_code}: {response.text[:200]}")

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

    def get_full_state(self, retries: int = 2, retry_delay: float = 10.0,
                       with_panels: bool = False) -> dict[str, Any]:
        """Return the lamp's current state using a single round-trip.

        Extracts the 'state' subfield from get_info() and returns a flat dict:
        {on, hue, sat, brightness, ct, colorMode}

        When `with_panels` is True, also include 'panel_ids' (sorted display-panel
        IDs from the SAME get_info() response — no extra device GET). A missing or
        malformed panelLayout yields panel_ids=[] without failing the state read.

        On NanoleafConnectionError, retries up to `retries` times with `retry_delay`
        seconds between attempts. Auth errors and HTTP errors are not retried.

        :returns: dict with current state values, or {} on failure
        """
        for attempt in range(retries + 1):
            try:
                info = self.get_info()
                state = info["state"]
                result = {
                    "on": state["on"]["value"],
                    "hue": state["hue"]["value"],
                    "sat": state["sat"]["value"],
                    "brightness": state["brightness"]["value"],
                    "ct": state["ct"]["value"],
                    "colorMode": state["colorMode"],
                }
                if with_panels:
                    try:
                        result["panel_ids"] = self._panel_ids_from_info(info)
                    except (KeyError, TypeError):
                        result["panel_ids"] = []
                return result
            except NanoleafAuthError as exc:
                logger.warning("get_full_state: auth error (%s)", exc)
                return {}
            except NanoleafConnectionError:
                if attempt < retries:
                    logger.debug(
                        "get_full_state: attempt %d/%d failed, retrying in %.0fs",
                        attempt + 1, retries + 1, retry_delay,
                    )
                    time.sleep(retry_delay)
                else:
                    return {}
            except NanoleafError:
                return {}
            except (KeyError, ValueError) as exc:
                logger.warning("get_full_state: unexpected API response shape: %s", exc)
                return {}
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

    # ------------------------------------------------------------------
    # Panel layout
    # ------------------------------------------------------------------

    def get_panel_ids(self) -> list[int]:
        """Return sorted list of display panel IDs from the device layout.

        Reads get_info()['panelLayout']['layout']['positionData'][i]['panelId'].
        Excludes shapeType 1 (Rhythm controller module) — it is not a display
        panel and must not appear in animData payloads.

        :raises NanoleafConnectionError: on network failure
        :raises NanoleafAuthError: on auth failure
        :raises NanoleafRequestError: on HTTP error
        :raises KeyError: if the response shape is unexpected
        """
        return self._panel_ids_from_info(self.get_info())

    @staticmethod
    def _panel_ids_from_info(info: dict) -> list[int]:
        """Sorted display-panel IDs from a get_info() response.

        Excludes shapeType 1 (Rhythm controller module) — it is not a display
        panel and must not appear in animData payloads.
        """
        position_data = info["panelLayout"]["layout"]["positionData"]
        return sorted(
            p["panelId"] for p in position_data
            if p.get("shapeType") != 1  # 1 = Rhythm module, not a display panel
        )

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------

    def write_effect(self, effect_data: dict) -> bool:
        """PUT /effects with {'write': effect_data}. Returns True on 2xx.

        effect_data must include 'command': 'display' (volatile — runs immediately,
        no NVRAM write). Never use 'command': 'add'; at one cron tick per 2 min
        that is ~500 k writes/year and degrades the device's flash storage.

        :returns: True if successful, otherwise False
        """
        try:
            # Large animData payloads (~5 KB for 51 panels) can take the lamp
            # several seconds to parse; use a generous read timeout.
            self._request("PUT", "/effects", data=json.dumps({"write": effect_data}),
                          timeout=(3, 20))
            return True
        except NanoleafConnectionError:
            # Transient network failure (timeout / refused) — re-raise so the
            # controller backs off and retries on the next tick.
            raise
        except NanoleafAuthError as exc:
            logger.warning("write_effect: auth error (%s)", exc)
            return False
        except NanoleafError as exc:
            # Non-transient (4xx/5xx, e.g. payload rejected) — return False so the
            # controller degrades to a brightness cap instead of backing off.
            logger.warning("write_effect: request rejected (%s)", exc)
            return False

    def set_color(
        self,
        rgb: tuple[int, int, int],
        on: Optional[bool] = None,
    ) -> bool:
        """Set the light colour from an RGB tuple via a batched /state call.

        Converts RGB (0–255 per channel) to HSB via color_helper.rgb_to_hsb,
        then sends a single batched PUT. Used by callers that work in RGB color space.

        :param rgb: (r, g, b) tuple, each channel 0–255
        :param on: if provided, include power state in the same call (True=on, False=off)
        :returns: True if successful, otherwise False
        """
        r, g, b = rgb
        if not all(0 <= c <= 255 for c in (r, g, b)):
            raise ValueError("RGB channels must each be between 0 and 255")
        return self.set_hsb(*rgb_to_hsb(r, g, b), on=on)
