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
    return nanoleafLight(
        name="TestLamp",
        ip="192.168.1.100",
        auth_token="test-token",
        print_errors=False,
    )


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

    def test_connection_error_raises_nanoleaf_connection_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError("refused")):
            with pytest.raises(NanoleafConnectionError):
                light._request("GET", "")

    def test_timeout_raises_nanoleaf_connection_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.Timeout("timed out")):
            with pytest.raises(NanoleafConnectionError):
                light._request("GET", "")

    def test_request_exception_raises_nanoleaf_connection_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.RequestException("error")):
            with pytest.raises(NanoleafConnectionError):
                light._request("GET", "")

    def test_401_raises_auth_error(self, light):
        with patch("requests.request", return_value=_mock_response(401)):
            with pytest.raises(NanoleafAuthError):
                light._request("GET", "")

    def test_403_raises_auth_error(self, light):
        with patch("requests.request", return_value=_mock_response(403)):
            with pytest.raises(NanoleafAuthError):
                light._request("GET", "")

    def test_400_raises_request_error(self, light):
        with patch("requests.request", return_value=_mock_response(400)):
            with pytest.raises(NanoleafRequestError):
                light._request("GET", "")

    def test_404_raises_request_error(self, light):
        with patch("requests.request", return_value=_mock_response(404)):
            with pytest.raises(NanoleafRequestError):
                light._request("GET", "")

    def test_500_raises_request_error(self, light):
        with patch("requests.request", return_value=_mock_response(500)):
            with pytest.raises(NanoleafRequestError):
                light._request("GET", "")

    def test_200_returns_response(self, light):
        with patch("requests.request", return_value=_mock_response(200, "ok")):
            resp = light._request("GET", "")
        assert resp.status_code == 200

    def test_204_returns_response(self, light):
        with patch("requests.request", return_value=_mock_response(204)):
            resp = light._request("PUT", "/state")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# get_info / get_full_state — single round-trip
# ---------------------------------------------------------------------------


class TestGetInfo:
    def test_get_info_makes_single_get(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))) as mock_req:
            result = light.get_info()
        assert mock_req.call_count == 1
        assert result == FULL_INFO

    def test_get_full_state_uses_single_round_trip(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))) as mock_req:
            state = light.get_full_state()
        assert mock_req.call_count == 1

    def test_get_full_state_extracts_correct_fields(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))):
            state = light.get_full_state()
        assert state == {
            "on": True,
            "hue": 30,
            "sat": 50,
            "brightness": 60,
            "ct": 4000,
            "colorMode": "hs",
        }

    def test_get_full_state_returns_empty_dict_on_network_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            state = light.get_full_state()
        assert state == {}

    def test_get_full_state_returns_empty_dict_on_auth_error(self, light):
        with patch("requests.request", return_value=_mock_response(401)):
            state = light.get_full_state()
        assert state == {}


# ---------------------------------------------------------------------------
# set_hsb — batched PUT /state
# ---------------------------------------------------------------------------


class TestSetHsb:
    def test_sends_batched_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = light.set_hsb(30, 50, 60)
        assert result is True
        _, kwargs = mock_req.call_args
        sent = json.loads(kwargs["data"])
        assert sent == {
            "hue": {"value": 30},
            "sat": {"value": 50},
            "brightness": {"value": 60, "duration": 0},
        }

    def test_sends_all_three_fields_in_one_call(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_hsb(10, 80, 25)
        assert mock_req.call_count == 1

    def test_duration_included_in_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_hsb(10, 80, 100, duration=50)
        _, kwargs = mock_req.call_args
        sent = json.loads(kwargs["data"])
        assert sent["brightness"]["duration"] == 50

    def test_returns_false_on_connection_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_hsb(10, 80, 25) is False

    def test_returns_false_on_http_error(self, light):
        with patch("requests.request", return_value=_mock_response(500)):
            assert light.set_hsb(10, 80, 25) is False

    @pytest.mark.parametrize("hue,sat,brightness", [
        (361, 50, 50),
        (-1, 50, 50),
        (180, 101, 50),
        (180, -1, 50),
        (180, 50, 101),
        (180, 50, -1),
    ])
    def test_raises_on_out_of_range(self, light, hue, sat, brightness):
        with pytest.raises(ValueError):
            light.set_hsb(hue, sat, brightness)


# ---------------------------------------------------------------------------
# set_color_temp_and_brightness — batched PUT /state
# ---------------------------------------------------------------------------


class TestSetColorTempAndBrightness:
    def test_sends_batched_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = light.set_color_temp_and_brightness(6000, 100)
        assert result is True
        _, kwargs = mock_req.call_args
        sent = json.loads(kwargs["data"])
        assert sent == {
            "ct": {"value": 6000},
            "brightness": {"value": 100, "duration": 0},
        }

    def test_single_request(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_color_temp_and_brightness(3000, 50)
        assert mock_req.call_count == 1

    def test_returns_false_on_connection_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_color_temp_and_brightness(6000, 100) is False

    @pytest.mark.parametrize("ct,brightness", [
        (1199, 50),
        (6501, 50),
        (3000, -1),
        (3000, 101),
    ])
    def test_raises_on_out_of_range(self, light, ct, brightness):
        with pytest.raises(ValueError):
            light.set_color_temp_and_brightness(ct, brightness)


# ---------------------------------------------------------------------------
# set_color — RGB to HSB conversion + batched PUT
# ---------------------------------------------------------------------------


class TestSetColor:
    @pytest.mark.parametrize("rgb, expected_hue, expected_sat, expected_val", [
        ((255, 0, 0), 0, 100, 100),
        ((0, 255, 0), 120, 100, 100),
        ((0, 0, 255), 240, 100, 100),
        ((255, 255, 255), 0, 0, 100),
        ((0, 0, 0), 0, 0, 0),
    ])
    def test_rgb_to_hsb_conversion(self, light, rgb, expected_hue, expected_sat, expected_val):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_color(rgb)
        _, kwargs = mock_req.call_args
        sent = json.loads(kwargs["data"])
        assert sent["hue"]["value"] == expected_hue
        assert sent["sat"]["value"] == expected_sat
        assert sent["brightness"]["value"] == expected_val

    def test_sends_single_batched_put(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            light.set_color((255, 0, 128))
        assert mock_req.call_count == 1

    def test_returns_false_on_failure(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_color((255, 0, 0)) is False

    @pytest.mark.parametrize("rgb", [
        (256, 0, 0),
        (-1, 0, 0),
        (0, 0, 256),
    ])
    def test_raises_on_out_of_range(self, light, rgb):
        with pytest.raises(ValueError):
            light.set_color(rgb)


# ---------------------------------------------------------------------------
# Power methods
# ---------------------------------------------------------------------------


class TestPower:
    def test_power_on_sends_correct_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = light.power_on()
        assert result is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {"on": {"value": True}}

    def test_power_off_sends_correct_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            result = light.power_off()
        assert result is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {"on": {"value": False}}

    def test_get_power_returns_true_when_on(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps({"value": True}))):
            assert light.get_power() is True

    def test_get_power_returns_false_on_network_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.get_power() is False

    def test_power_on_returns_false_on_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.Timeout()):
            assert light.power_on() is False

    def test_power_off_returns_false_on_error(self, light):
        with patch("requests.request", return_value=_mock_response(500)):
            assert light.power_off() is False


# ---------------------------------------------------------------------------
# check_heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_returns_true_when_reachable(self, light):
        with patch("requests.request", return_value=_mock_response(200, json.dumps(FULL_INFO))):
            assert light.check_heartbeat() is True

    def test_returns_false_on_connection_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.check_heartbeat() is False

    def test_returns_false_on_timeout(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.Timeout()):
            assert light.check_heartbeat() is False


# ---------------------------------------------------------------------------
# toggle_power
# ---------------------------------------------------------------------------


class TestTogglePower:
    def test_turns_off_when_on(self, light):
        with patch("requests.request", side_effect=[
            _mock_response(200, json.dumps({"value": True})),   # get_power
            _mock_response(204),                                 # power_off
        ]):
            result = light.toggle_power()
        assert result is True

    def test_turns_on_when_off(self, light):
        with patch("requests.request", side_effect=[
            _mock_response(200, json.dumps({"value": False})),  # get_power
            _mock_response(204),                                 # power_on
        ]):
            result = light.toggle_power()
        assert result is True

    def test_returns_false_when_power_change_fails(self, light):
        with patch("requests.request", side_effect=[
            _mock_response(200, json.dumps({"value": True})),   # get_power succeeds
            _mock_response(500),                                 # power_off fails
        ]):
            result = light.toggle_power()
        assert result is False


# ---------------------------------------------------------------------------
# Individual single-field setters
# ---------------------------------------------------------------------------


class TestIndividualSetters:
    def test_set_brightness_sends_correct_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            assert light.set_brightness(75) is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {"brightness": {"value": 75, "duration": 0}}

    def test_set_brightness_raises_on_out_of_range(self, light):
        with pytest.raises(ValueError):
            light.set_brightness(101)

    def test_set_brightness_returns_false_on_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_brightness(50) is False

    def test_set_hue_sends_correct_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            assert light.set_hue(180) is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {"hue": {"value": 180}}

    def test_set_hue_raises_on_out_of_range(self, light):
        with pytest.raises(ValueError):
            light.set_hue(361)

    def test_set_hue_returns_false_on_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_hue(90) is False

    def test_set_saturation_sends_correct_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            assert light.set_saturation(80) is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {"sat": {"value": 80}}

    def test_set_saturation_raises_on_out_of_range(self, light):
        with pytest.raises(ValueError):
            light.set_saturation(-1)

    def test_set_saturation_returns_false_on_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_saturation(50) is False

    def test_set_color_temp_sends_correct_body(self, light):
        with patch("requests.request", return_value=_mock_response(204)) as mock_req:
            assert light.set_color_temp(4000) is True
        _, kwargs = mock_req.call_args
        assert json.loads(kwargs["data"]) == {"ct": {"value": 4000}}

    def test_set_color_temp_raises_on_out_of_range(self, light):
        with pytest.raises(ValueError):
            light.set_color_temp(7000)

    def test_set_color_temp_returns_false_on_error(self, light):
        with patch("requests.request", side_effect=requests_lib.exceptions.ConnectionError()):
            assert light.set_color_temp(3000) is False


# ---------------------------------------------------------------------------
# __error_check (kept for future use; not called by current public methods)
# ---------------------------------------------------------------------------


class TestErrorCheck:
    def test_returns_true_for_200(self, light):
        assert light._nanoleafLight__error_check(200) is True

    def test_returns_true_for_204(self, light):
        assert light._nanoleafLight__error_check(204) is True

    def test_returns_false_for_400(self, light):
        assert light._nanoleafLight__error_check(400) is False

    def test_returns_false_for_401(self, light):
        assert light._nanoleafLight__error_check(401) is False

    def test_returns_false_for_500(self, light):
        assert light._nanoleafLight__error_check(500) is False
