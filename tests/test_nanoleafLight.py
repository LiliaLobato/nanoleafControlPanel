"""Unit tests for nanoleafLight.py — all HTTP calls mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests as requests_lib

from nanoleafLight import (
    nanoleafLight,
    NanoleafAuthError,
    NanoleafConnectionError,
    NanoleafError,
    NanoleafRequestError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def light():
    return nanoleafLight(name="TestLamp", ip="192.168.1.100", auth_token="test-token")


def _mock_response(status_code: int, body: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    return resp


FULL_INFO = {
    "name": "TestLamp",
    "state": {
        "on": {"value": True},
        "hue": {"value": 30},
        "sat": {"value": 50},
        "brightness": {"value": 60},
        "ct": {"value": 4000},
        "colorMode": "hs",
    },
}

# ---------------------------------------------------------------------------
# _request helper — timeouts and exception mapping
# ---------------------------------------------------------------------------


class TestRequestHelper:
    def test_timeout_passed_by_default(self, light):
        with patch("requests.request", return_value=_mock_response(200)) as mock_req:
            light._request("GET", "")
        _, kwargs = mock_req.call_args
        assert kwargs["timeout"] == (3, 5)

    # All three exception types are intentional: ConnectionError+Timeout hit the
    # first except clause; RequestException hits the second (broader) one.
    @pytest.mark.parametrize("exc_class", [
        requests_lib.exceptions.ConnectionError,
        requests_lib.exceptions.Timeout,
        requests_lib.exceptions.RequestException,
    ])
    def test_network_exceptions_raise_connection_error(self, light, exc_class):
        with patch("requests.request", side_effect=exc_class("error")):
            with pytest.raises(NanoleafConnectionError):
                light._request("GET", "")

    def test_auth_code_raises_auth_error(self, light):
        with patch("requests.request", return_value=_mock_response(401)):
            with pytest.raises(NanoleafAuthError):
                light._request("GET", "")

    def test_http_error_raises_request_error(self, light):
        with patch("requests.request", return_value=_mock_response(500)):
            with pytest.raises(NanoleafRequestError):
                light._request("GET", "")

    def test_success_returns_response(self, light):
        with patch("requests.request", return_value=_mock_response(200, "ok")):
            resp = light._request("GET", "")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# get_info / get_full_state — single round-trip
# ---------------------------------------------------------------------------


class TestGetInfo:
    def test_get_info_makes_single_get(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))) as mock_req:
            result = light.get_info()
        assert mock_req.call_count == 1
        assert result == FULL_INFO

    def test_get_full_state_extracts_correct_fields(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))) as mock_req:
            state = light.get_full_state()
        assert mock_req.call_count == 1
        assert state == {"on": True, "hue": 30, "sat": 50, "brightness": 60, "ct": 4000, "colorMode": "hs"}

    def test_get_full_state_returns_empty_dict_on_failure(self, light):
        with patch("requests.request", return_value=_mock_response(401)):
            assert light.get_full_state() == {}


# ---------------------------------------------------------------------------
# set_hsb — batched PUT /state
# ---------------------------------------------------------------------------


class TestSetHsb:
    def test_sends_batched_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = light.set_hsb(30, 50, 60)
        assert result is True
        assert mock_req.call_count == 1
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {
            "hue": {"value": 30},
            "sat": {"value": 50},
            "brightness": {"value": 60, "duration": 0},
        }

    def test_duration_included_in_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_hsb(10, 80, 100, duration=50)
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"])["brightness"]["duration"] == 50

    def test_returns_false_on_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_hsb(10, 80, 25) is False

    @pytest.mark.parametrize("hue,sat,brightness", [
        (361, 50, 50),   # hue out of range
        (180, 101, 50),  # sat out of range
        (180, 50, 101),  # brightness out of range
    ])
    def test_raises_on_out_of_range(self, light, hue, sat, brightness):
        with pytest.raises(ValueError):
            light.set_hsb(hue, sat, brightness)

    def test_on_false_included_in_body(self, light):
        """on=False batches power-off with color so pre-staging never turns the lamp on."""
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_hsb(30, 50, 60, on=False)
        _, kwargs = mock_req.call_args
        body = json.loads(kwargs["data"])
        assert body["on"] == {"value": False}, f"Expected on=False in body, got {body}"

    def test_on_true_included_in_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_hsb(30, 50, 60, on=True)
        _, kwargs = mock_req.call_args
        body = json.loads(kwargs["data"])
        assert body["on"] == {"value": True}, f"Expected on=True in body, got {body}"

    def test_on_none_omitted_from_body(self, light):
        """Default (on=None) must not include 'on' key — no unintended power changes."""
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_hsb(30, 50, 60)
        _, kwargs = mock_req.call_args
        body = json.loads(kwargs["data"])
        assert "on" not in body, f"'on' should be absent when not specified, got {body}"


# ---------------------------------------------------------------------------
# set_color_temp_and_brightness — batched PUT /state
# ---------------------------------------------------------------------------


class TestSetColorTempAndBrightness:
    def test_sends_batched_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = light.set_color_temp_and_brightness(6000, 100)
        assert result is True
        assert mock_req.call_count == 1
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {
            "ct": {"value": 6000},
            "brightness": {"value": 100, "duration": 0},
        }

    def test_returns_false_on_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_color_temp_and_brightness(6000, 100) is False

    @pytest.mark.parametrize("ct,brightness", [
        (1199, 50),   # ct too low
        (3000, 101),  # brightness too high
    ])
    def test_raises_on_out_of_range(self, light, ct, brightness):
        with pytest.raises(ValueError):
            light.set_color_temp_and_brightness(ct, brightness)

    def test_on_false_included_in_body(self, light):
        """on=False batches power-off with color so pre-staging never turns the lamp on."""
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_color_temp_and_brightness(6000, 100, on=False)
        _, kwargs = mock_req.call_args
        body = json.loads(kwargs["data"])
        assert body["on"] == {"value": False}, f"Expected on=False in body, got {body}"

    def test_on_none_omitted_from_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_color_temp_and_brightness(6000, 100)
        _, kwargs = mock_req.call_args
        body = json.loads(kwargs["data"])
        assert "on" not in body, f"'on' should be absent when not specified, got {body}"


# ---------------------------------------------------------------------------
# set_color — RGB to HSB conversion + batched PUT
# ---------------------------------------------------------------------------


class TestSetColor:
    @pytest.mark.parametrize("rgb, expected_hue, expected_sat, expected_val", [
        ((255, 0, 0),   0,   100, 100),  # red
        ((0, 255, 0),   120, 100, 100),  # green
        ((255, 255, 255), 0,   0,   100),  # white — sat=0 edge case
        ((0, 0, 0),     0,   0,   0),    # black — brightness=0 edge case
    ])
    def test_rgb_to_hsb_conversion(self, light, rgb, expected_hue, expected_sat, expected_val):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_color(rgb)
        assert mock_req.call_count == 1
        _, kwargs = mock_req.call_args
        sent = json.loads(kwargs["data"])
        assert sent["hue"]["value"] == expected_hue
        assert sent["sat"]["value"] == expected_sat
        assert sent["brightness"]["value"] == expected_val

    def test_returns_false_on_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_color((255, 0, 0)) is False

    def test_raises_on_out_of_range(self, light):
        with pytest.raises(ValueError):
            light.set_color((256, 0, 0))


# ---------------------------------------------------------------------------
# Power methods
# ---------------------------------------------------------------------------


class TestPower:
    @pytest.mark.parametrize("method,expected_body", [
        ("power_on",  {"on": {"value": True}}),
        ("power_off", {"on": {"value": False}}),
    ])
    def test_power_sends_correct_body(self, light, method, expected_body):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = getattr(light, method)()
        assert result is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == expected_body

    def test_get_power_returns_true_when_on(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps({"value": True}))):
            assert light.get_power() is True

    def test_power_returns_false_on_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.power_on() is False


# ---------------------------------------------------------------------------
# check_heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_returns_true_when_reachable(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))):
            assert light.check_heartbeat() is True

    def test_returns_false_on_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.check_heartbeat() is False
