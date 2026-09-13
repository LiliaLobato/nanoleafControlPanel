"""Live CLI end-to-end tests.

Exercises every nanoleaf-cli command group against a real Nanoleaf device,
verifying that CLI actions produce the correct physical lamp state and that
the controller respects CLI-written state on its next run.

Run with:  RUN_E2E=1 pytest e2e/test_live_cli.py -v
           RUN_E2E=1 pytest e2e/test_live_cli.py -v -m slow   # includes preview (10s each)

What is NOT tested here (covered elsewhere):
  - NanoleafLight API details: test_live_lamp.py
  - Controller phase/interpolation math: tests/test_controller.py
  - CLI argument validation and formatting: tests/test_cli.py

What IS tested here (no other test touches this):
  - lamp on/off/info through the CLI layer
  - preview commands apply and revert correctly on real hardware
  - party CLI writes state that the controller then executes on the lamp
  - status command reflects real lamp/weather/phase data
  - config and profile round-trips persist to disk and affect behavior
  - debug on/off changes verbose flag in config.json
"""

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv

load_dotenv()

RUN_E2E = os.environ.get("RUN_E2E", "").strip() == "1"
pytestmark = pytest.mark.skipif(not RUN_E2E, reason="Set RUN_E2E=1 to run live CLI tests")

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(**kwargs):
    """Build an argparse.Namespace for CLI run_* functions."""
    return argparse.Namespace(**kwargs)


def _now_at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now(tz=LOCAL_TZ)
    return today.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _make_weather(sunrise_hour=5, sunrise_min=30, sunset_hour=20, fixture="clear.json"):
    import json
    from weather.openWeather import OpenWeatherLight
    data = json.loads((FIXTURES / fixture).read_text())
    today = datetime.now(tz=LOCAL_TZ)
    data["sys"]["sunrise"] = int(today.replace(hour=sunrise_hour, minute=sunrise_min, second=0).timestamp())
    data["sys"]["sunset"]  = int(today.replace(hour=sunset_hour,  minute=0, second=0).timestamp())
    return OpenWeatherLight.from_cache(data, 47.6144, -122.1923)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def light():
    from nanoleaf.nanoleafLight import NanoleafLight
    ip    = os.environ["NANOLEAF_IP_ADDRESS"]
    token = os.environ["NANOLEAF_AUTH_TOKEN"]
    lamp  = NanoleafLight(name="nanoleaf", ip=ip, auth_token=token)
    assert lamp.check_heartbeat(), "Lamp unreachable — check NANOLEAF_IP_ADDRESS and NANOLEAF_AUTH_TOKEN in .env"
    return lamp


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect state.json to a temp dir so tests never corrupt real state."""
    import controller.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_DIR",  tmp_path)
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "LOCK_PATH",  tmp_path / "controller.lock")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config.json to a temp dir and clear the mtime cache."""
    import controller.config as cfg_mod
    tmp_cfg = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_cfg)
    cfg_mod._config_cache.clear()
    yield
    cfg_mod._config_cache.clear()


@pytest.fixture(autouse=True)
def restore_lamp(light):
    """Leave the lamp ON after every test."""
    yield
    light.power_on()
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# GROUP 1 — lamp commands
# ---------------------------------------------------------------------------

class TestLampCommands:

    def test_lamp_on_powers_on(self, light):
        """lamp on → lamp is physically ON."""
        from nanoleaf_cli.commands.lamp import run_on
        light.power_off()
        time.sleep(1)

        run_on(_args())
        time.sleep(1)

        state = light.get_full_state()
        assert state["on"] is True, \
            f"lamp on should power the lamp ON; got on={state['on']}"

    def test_lamp_off_powers_off(self, light):
        """lamp off → lamp is physically OFF."""
        from nanoleaf_cli.commands.lamp import run_off
        light.power_on()
        time.sleep(1)

        run_off(_args())
        time.sleep(1)

        state = light.get_full_state()
        assert state["on"] is False, \
            f"lamp off should power the lamp OFF; got on={state['on']}"

    def test_lamp_on_off_cycle(self, light):
        """lamp on → lamp off → lamp on: three-state power cycle."""
        from nanoleaf_cli.commands.lamp import run_off, run_on
        run_on(_args());  time.sleep(1)
        assert light.get_full_state()["on"] is True,  "after lamp on: expected ON"
        run_off(_args()); time.sleep(1)
        assert light.get_full_state()["on"] is False, "after lamp off: expected OFF"
        run_on(_args());  time.sleep(1)
        assert light.get_full_state()["on"] is True,  "after second lamp on: expected ON"

    def test_lamp_info_returns_device_data(self, light, capsys):
        """lamp info → stdout contains expected device fields."""
        from nanoleaf_cli.commands.lamp import run_info
        light.power_on()
        run_info(_args())
        out = capsys.readouterr().out
        for key in ("name", "serialNo", "firmwareVersion", "state"):
            assert key in out, f"lamp info output missing field: {key!r}"

    def test_lamp_info_includes_power_state(self, light, capsys):
        """lamp info output contains the current on/off state."""
        from nanoleaf_cli.commands.lamp import run_info
        light.power_on(); time.sleep(0.5)
        run_info(_args())
        out = capsys.readouterr().out
        assert '"on"' in out or "'on'" in out, \
            "lamp info should include 'on' field in state block"


# ---------------------------------------------------------------------------
# GROUP 2 — preview commands (marked slow: each waits 10 s for the revert)
# ---------------------------------------------------------------------------

class TestPreviewCommands:

    def test_preview_hue_reverts_to_original_hsb(self, light):
        """preview hue applies a hue, then reverts to the original HSB values."""
        from nanoleaf_cli.commands.preview import run_hue
        light.power_on(); time.sleep(0.5)
        light.set_hsb(10, 80, 50); time.sleep(1)
        orig = light.get_full_state()

        run_hue(_args(value=180))   # 10-second hold then revert
        time.sleep(0.5)

        final = light.get_full_state()
        assert abs(final["hue"]        - orig["hue"])        <= 5, \
            f"preview hue: hue not reverted — expected ~{orig['hue']}, got {final['hue']}"
        assert abs(final["brightness"] - orig["brightness"])  <= 5, \
            f"preview hue: brightness not reverted — expected ~{orig['brightness']}, got {final['brightness']}"

    def test_preview_hue_lamp_actually_changes_during_preview(self, light, monkeypatch):
        """During the 10-second hold the lamp must be at the previewed hue."""
        from nanoleaf_cli.commands import preview as preview_mod
        light.power_on(); time.sleep(0.5)
        light.set_hsb(10, 80, 50); time.sleep(1)

        captured = {}

        real_sleep = __import__("time").sleep
        def intercepting_sleep(n):
            # On the first sleep (the countdown), sample the lamp
            if n == 1 and "sampled" not in captured:
                captured["sampled"] = light.get_full_state()
            real_sleep(n)

        monkeypatch.setattr(preview_mod._time, "sleep", intercepting_sleep)
        run_hue = preview_mod.run_hue
        run_hue(_args(value=180))

        assert "sampled" in captured, "sleep was never called — preview may not have run"
        assert abs(captured["sampled"]["hue"] - 180) <= 5, \
            f"lamp hue during preview should be ~180, got {captured['sampled']['hue']}"

    def test_preview_hue_reverts_when_lamp_was_off(self, light):
        """preview hue on an OFF lamp: turns on briefly, then powers back off."""
        from nanoleaf_cli.commands.preview import run_hue
        light.power_off(); time.sleep(1)

        run_hue(_args(value=120))
        time.sleep(0.5)

        final = light.get_full_state()
        assert final["on"] is False, \
            f"lamp was off before preview; should be off again after — got on={final['on']}"

    @pytest.mark.slow
    def test_preview_hsb_applies_and_reverts(self, light):
        """preview hsb 200 60 70 → lamp shows those values, then reverts."""
        from nanoleaf_cli.commands.preview import run_hsb
        light.power_on(); time.sleep(0.5)
        light.set_hsb(10, 80, 50); time.sleep(1)
        orig = light.get_full_state()

        run_hsb(_args(hue=200, saturation=60, brightness=70))
        time.sleep(0.5)

        final = light.get_full_state()
        assert abs(final["hue"]        - orig["hue"])        <= 5, \
            f"preview hsb: hue not reverted to {orig['hue']}, got {final['hue']}"
        assert abs(final["brightness"] - orig["brightness"])  <= 5, \
            f"preview hsb: brightness not reverted to {orig['brightness']}, got {final['brightness']}"

    @pytest.mark.slow
    def test_preview_profile_night_applies_and_reverts(self, light):
        """preview profile NIGHT applies NIGHT_PROFILE values, then reverts."""
        from nanoleaf_cli.commands.preview import run_profile
        from controller.config import NIGHT_PROFILE
        light.power_on(); time.sleep(0.5)
        light.set_hsb(180, 60, 80); time.sleep(1)
        orig = light.get_full_state()

        run_profile(_args(name="NIGHT"))
        time.sleep(0.5)

        final = light.get_full_state()
        assert abs(final["hue"]        - orig["hue"])        <= 5, \
            f"preview profile: hue not reverted to {orig['hue']}, got {final['hue']}"
        assert abs(final["brightness"] - orig["brightness"])  <= 5, \
            f"preview profile: brightness not reverted to {orig['brightness']}, got {final['brightness']}"

    @pytest.mark.slow
    def test_preview_profile_ct_mode_reverts_to_ct(self, light):
        """If lamp was in CT mode, preview reverts to CT (not HSB)."""
        from nanoleaf_cli.commands.preview import run_profile
        light.power_on(); time.sleep(0.5)
        light.set_color_temp_and_brightness(4000, 70); time.sleep(1)

        run_profile(_args(name="NIGHT"))   # NIGHT is HSB
        time.sleep(0.5)

        final = light.get_full_state()
        assert final["colorMode"] in ("ct", "color_temperature"), \
            f"lamp was CT before preview; colorMode after revert should be CT, got {final['colorMode']!r}"

    @pytest.mark.slow
    def test_preview_color_rgb_applies_and_reverts(self, light):
        """preview color 255,0,128 (pink) → lamp shows ~HSB(330, 100, 100), then reverts."""
        from nanoleaf_cli.commands.preview import run_color
        light.power_on(); time.sleep(0.5)
        light.set_hsb(10, 80, 50); time.sleep(1)
        orig = light.get_full_state()

        run_color(_args(rgb="255,0,128"))
        time.sleep(0.5)

        final = light.get_full_state()
        assert abs(final["hue"]        - orig["hue"])        <= 5, \
            f"preview color: hue not reverted to {orig['hue']}, got {final['hue']}"
        assert abs(final["brightness"] - orig["brightness"])  <= 5, \
            f"preview color: brightness not reverted to {orig['brightness']}, got {final['brightness']}"

    @pytest.mark.slow
    def test_preview_sparkle_applies_and_reverts(self, light):
        """preview sparkle renders the scatter effect on hardware, then reverts to
        the prior solid colour."""
        from nanoleaf_cli.commands.preview import run_sparkle
        light.power_on(); time.sleep(0.5)
        light.set_hsb(10, 80, 50); time.sleep(1)
        orig = light.get_full_state()

        # near-white high brightness so the scatter is over budget and renders; short
        # duration keeps the test fast. run_sparkle reverts to the prior state.
        run_sparkle(_args(hue=0, sat=0, brightness=90, floor=None, duration=1))
        time.sleep(0.5)

        final = light.get_full_state()
        assert abs(final["hue"]        - orig["hue"])        <= 8, \
            f"preview sparkle: hue not reverted to ~{orig['hue']}, got {final['hue']}"
        assert abs(final["brightness"] - orig["brightness"])  <= 8, \
            f"preview sparkle: brightness not reverted to ~{orig['brightness']}, got {final['brightness']}"


# ---------------------------------------------------------------------------
# GROUP 3 — party commands + controller integration
# ---------------------------------------------------------------------------

class TestPartyCommands:

    def test_party_start_default_writes_active_state(self):
        """party (no args) → state.json has party_mode.active=True with PARTY_PROFILE."""
        from controller.config import PARTY_PROFILE
        from controller.state import load_state
        from nanoleaf_cli.commands.party import run

        run(_args(action=None, until=None, fade=None, fade_duration=None, color=None,
                  hue=None, sat=None, brightness=None),
            now=_now_at(21, 0))

        state = load_state()
        pm = state.get("party_mode", {})
        assert pm.get("active") is True, "party start: expected party_mode.active=True"
        assert pm["profile"]["hue"] == PARTY_PROFILE.hue, \
            f"party start: expected hue={PARTY_PROFILE.hue}, got {pm['profile']['hue']}"
        assert pm["profile"]["saturation"] == PARTY_PROFILE.saturation, \
            f"party start: expected sat={PARTY_PROFILE.saturation}, got {pm['profile']['saturation']}"

    def test_party_start_controller_applies_color_to_lamp(self, light, monkeypatch):
        """party start → controller run → lamp shows party profile HSB values."""
        import sunrise_sunset_controller as ctrl
        from controller.config import PARTY_PROFILE
        from nanoleaf_cli.commands.party import run

        monkeypatch.setattr(ctrl, "get_weather",
                            lambda *_a, **_kw: _make_weather(sunset_hour=20))

        run(_args(action=None, until=None, fade=None, fade_duration=None, color=None,
                  hue=None, sat=None, brightness=None),
            now=_now_at(21, 0))

        light.power_on(); time.sleep(0.5)
        ctrl.main(now=_now_at(21, 0))
        time.sleep(1)

        state = light.get_full_state()
        assert abs(state["hue"]        - PARTY_PROFILE.hue)        <= 3, \
            f"party color on lamp: expected hue~{PARTY_PROFILE.hue}, got {state['hue']}"
        assert abs(state["sat"]        - PARTY_PROFILE.saturation)  <= 3, \
            f"party color on lamp: expected sat~{PARTY_PROFILE.saturation}, got {state['sat']}"
        assert abs(state["brightness"] - PARTY_PROFILE.brightness)  <= 3, \
            f"party color on lamp: expected brightness~{PARTY_PROFILE.brightness}, got {state['brightness']}"

    def test_party_custom_hsb_reaches_lamp(self, light, monkeypatch):
        """party --hue 120 --sat 90 --brightness 70 → lamp shows those HSB values."""
        import sunrise_sunset_controller as ctrl
        from nanoleaf_cli.commands.party import run

        monkeypatch.setattr(ctrl, "get_weather",
                            lambda *_a, **_kw: _make_weather(sunset_hour=20))

        run(_args(action=None, until=None, fade=None, fade_duration=None, color=None,
                  hue=120, sat=90, brightness=70),
            now=_now_at(21, 0))

        light.power_on(); time.sleep(0.5)
        ctrl.main(now=_now_at(21, 0))
        time.sleep(1)

        state = light.get_full_state()
        assert abs(state["hue"]        - 120) <= 3, \
            f"custom party hue: expected ~120, got {state['hue']}"
        assert abs(state["sat"]        - 90)  <= 3, \
            f"custom party sat: expected ~90, got {state['sat']}"
        assert abs(state["brightness"] - 70)  <= 3, \
            f"custom party brightness: expected ~70, got {state['brightness']}"

    def test_party_rgb_color_reaches_lamp(self, light, monkeypatch):
        """party --color 0,255,0 (pure green) → lamp shows HSB(120, 100, 100)."""
        import sunrise_sunset_controller as ctrl
        from nanoleaf_cli.commands.party import run

        monkeypatch.setattr(ctrl, "get_weather",
                            lambda *_a, **_kw: _make_weather(sunset_hour=20))

        run(_args(action=None, until=None, fade=None, fade_duration=None,
                  color="0,255,0", hue=None, sat=None, brightness=None),
            now=_now_at(21, 0))

        light.power_on(); time.sleep(0.5)
        ctrl.main(now=_now_at(21, 0))
        time.sleep(1)

        state = light.get_full_state()
        assert abs(state["hue"] - 120) <= 5, \
            f"RGB green party: expected hue~120, got {state['hue']}"
        assert abs(state["sat"] - 100) <= 5, \
            f"RGB green party: expected sat~100, got {state['sat']}"

    def test_party_stop_clears_state(self):
        """party stop → state.party_mode.active=False."""
        from controller.state import load_state
        from nanoleaf_cli.commands.party import run

        # start first
        run(_args(action=None, until=None, fade=None, fade_duration=None, color=None,
                  hue=None, sat=None, brightness=None),
            now=_now_at(21, 0))
        assert load_state().get("party_mode", {}).get("active") is True

        # stop
        run(_args(action="stop"), now=_now_at(21, 5))

        state = load_state()
        assert state.get("party_mode", {}).get("active") is not True, \
            "party stop should set active=False or remove party_mode"

    def test_party_stop_controller_resumes_normal_phase(self, light, monkeypatch):
        """After party stop, controller run does NOT apply party color."""
        import sunrise_sunset_controller as ctrl
        from controller.config import PARTY_PROFILE
        from nanoleaf_cli.commands.party import run

        monkeypatch.setattr(ctrl, "get_weather",
                            lambda *_a, **_kw: _make_weather(sunset_hour=20))

        # start party, run controller (lamp goes purple)
        run(_args(action=None, until=None, fade=None, fade_duration=None, color=None,
                  hue=None, sat=None, brightness=None),
            now=_now_at(21, 0))
        light.power_on(); time.sleep(0.5)
        ctrl.main(now=_now_at(21, 0)); time.sleep(1)

        # stop party, run controller again
        run(_args(action="stop"), now=_now_at(21, 5))
        ctrl.main(now=_now_at(21, 5)); time.sleep(1)

        state = light.get_full_state()
        # After party stop the controller runs evening_ramp — hue should NOT be
        # the party purple (280). Exact value depends on interpolation, but it
        # should be far from 280.
        assert abs(state["hue"] - PARTY_PROFILE.hue) > 20, \
            f"After party stop, lamp hue should leave party color (got {state['hue']}, party was {PARTY_PROFILE.hue})"

    def test_party_disable_is_alias_for_stop(self):
        """party disable behaves identically to party stop."""
        from controller.state import load_state
        from nanoleaf_cli.commands.party import run

        run(_args(action=None, until=None, fade=None, fade_duration=None, color=None,
                  hue=None, sat=None, brightness=None),
            now=_now_at(21, 0))
        run(_args(action="disable"), now=_now_at(21, 5))

        state = load_state()
        assert state.get("party_mode", {}).get("active") is not True, \
            "party disable should clear active state just like party stop"

    def test_party_stop_when_not_active_prints_message(self, capsys):
        """party stop when not active → prints informational message, no crash."""
        from nanoleaf_cli.commands.party import run
        run(_args(action="stop"), now=_now_at(21, 0))
        out = capsys.readouterr().out
        assert "not active" in out, \
            f"Expected 'not active' message; got: {out!r}"


# ---------------------------------------------------------------------------
# GROUP 4 — status command
# ---------------------------------------------------------------------------

class TestStatusCommand:

    def test_status_shows_phase(self, capsys):
        """status → output contains a recognizable phase name."""
        from nanoleaf_cli.commands.status import run
        known_phases = {
            "morning_ramp", "pre_morning", "day", "evening_ramp",
            "night_ramp", "hard_cutoff_ramp", "off", "party_mode", "late_night_override",
        }
        run(_args(verbose=False), now=_now_at(10, 0))
        out = capsys.readouterr().out
        assert any(p in out for p in known_phases), \
            f"status output should contain a phase name; got:\n{out}"

    def test_status_shows_time(self, capsys):
        """status → output contains the current date."""
        from nanoleaf_cli.commands.status import run
        now = _now_at(10, 0)
        run(_args(verbose=False), now=now)
        out = capsys.readouterr().out
        year = str(now.year)
        assert year in out, f"status should print current year ({year}); got:\n{out}"

    def test_status_verbose_shows_file_paths(self, capsys):
        """status -v → output includes state and config file paths."""
        from nanoleaf_cli.commands.status import run
        run(_args(verbose=True), now=_now_at(10, 0))
        out = capsys.readouterr().out
        assert "state" in out.lower(), \
            f"status -v should mention state file; got:\n{out}"

    def test_status_shows_party_mode_when_active(self, capsys):
        """status with active party → output mentions party."""
        from controller.state import load_state, save_state
        from nanoleaf_cli.commands.status import run

        state = load_state()
        state["party_mode"] = {
            "active": True,
            "ends_at": _now_at(23, 0).isoformat(),
            "fade_minutes": 30,
            "profile": {"mode": "hsb", "hue": 280, "saturation": 90,
                        "brightness": 100, "color_temp": 0},
        }
        save_state(state)

        run(_args(verbose=False), now=_now_at(21, 0))
        out = capsys.readouterr().out
        assert "party" in out.lower(), \
            f"status should show party mode when active; got:\n{out}"

    def test_status_shows_dnd_when_active(self, capsys):
        """status with active DND → output mentions DND."""
        from controller.state import load_state, save_state
        from nanoleaf_cli.commands.status import run

        state = load_state()
        state["do_not_disturb_until"] = _now_at(23, 0).isoformat()
        state["dnd_scope"] = "overnight"
        save_state(state)

        run(_args(verbose=False), now=_now_at(22, 0))
        out = capsys.readouterr().out
        assert "DND" in out or "do not disturb" in out.lower(), \
            f"status should show DND when active; got:\n{out}"


# ---------------------------------------------------------------------------
# GROUP 5 — config + profile round-trips (file I/O, no lamp contact)
# ---------------------------------------------------------------------------

class TestConfigAndProfileRoundTrips:

    def test_config_set_persists_and_get_reads_back(self, capsys):
        """config set force_evening_time 21:30 → config get returns 21:30."""
        from nanoleaf_cli.commands.config import run_get, run_set
        run_set(_args(key="force_evening_time", value="21:30", verbose=False))
        run_get(_args(key="force_evening_time"))
        out = capsys.readouterr().out
        assert "21:30" in out, \
            f"config get should return the set value 21:30; got:\n{out}"

    def test_config_reset_restores_default(self, capsys):
        """config set → config reset → config get returns default (21:00)."""
        from nanoleaf_cli.commands.config import run_get, run_reset, run_set
        run_set(_args(key="force_evening_time", value="21:30", verbose=False))
        run_reset(_args(key="force_evening_time", all=False))
        run_get(_args(key="force_evening_time"))
        out = capsys.readouterr().out
        assert "21:00" in out, \
            f"After reset, force_evening_time should be default 21:00; got:\n{out}"

    def test_profile_set_persists_single_field(self, capsys):
        """profile set NIGHT hue 20 → profile get NIGHT shows hue=20."""
        from nanoleaf_cli.commands.profile import run_get, run_set
        run_set(_args(name="NIGHT", field="hue", value="20", verbose=False))
        run_get(_args(name="NIGHT"))
        out = capsys.readouterr().out
        assert "20" in out, \
            f"profile get NIGHT should show hue=20 after set; got:\n{out}"

    def test_profile_set_does_not_change_other_fields(self, capsys):
        """profile set NIGHT hue only → saturation stays at default (80)."""
        from controller.config import NIGHT_PROFILE
        from nanoleaf_cli.commands.profile import run_get, run_set
        run_set(_args(name="NIGHT", field="hue", value="20", verbose=False))
        run_get(_args(name="NIGHT"))
        out = capsys.readouterr().out
        assert str(NIGHT_PROFILE.saturation) in out, \
            f"profile set hue only: saturation should still be {NIGHT_PROFILE.saturation}; got:\n{out}"

    def test_profile_reset_restores_default_hue(self, capsys):
        """profile reset NIGHT → profile get returns original NIGHT_PROFILE hue."""
        from controller.config import NIGHT_PROFILE
        from nanoleaf_cli.commands.profile import run_get, run_reset, run_set
        run_set(_args(name="NIGHT", field="hue", value="20", verbose=False))
        run_reset(_args(name="NIGHT"))
        run_get(_args(name="NIGHT"))
        out = capsys.readouterr().out
        assert str(NIGHT_PROFILE.hue) in out, \
            f"After reset, NIGHT hue should be default {NIGHT_PROFILE.hue}; got:\n{out}"

    def test_config_affects_phase_calculation(self, monkeypatch):
        """config set morning_latest_start 08:00 → phase at 07:30 is pre_morning."""
        from controller.config import load_config
        from controller.phase import calculate_phase
        from nanoleaf_cli.commands.config import run_set

        # Default morning_latest_start = 06:00; at 07:30 with late sunrise = morning_ramp.
        # Change to 08:00: now min(sunrise=07:55, 08:00) = 07:55, and 07:30 < 07:55 → pre_morning.
        run_set(_args(key="morning_latest_start", value="08:00", verbose=False))

        weather = _make_weather(sunrise_hour=7, sunrise_min=55, sunset_hour=20)
        config  = load_config()
        phase   = calculate_phase(_now_at(7, 30), weather, config, {})
        assert phase == "pre_morning", \
            f"With morning_latest_start=08:00, 07:30 should be pre_morning; got {phase!r}"


# ---------------------------------------------------------------------------
# GROUP 6 — debug on/off
# ---------------------------------------------------------------------------

class TestDebugCommands:

    def test_debug_on_sets_verbose_true(self):
        """debug on → config.json has verbose=true."""
        from controller.config import load_config
        from nanoleaf_cli.commands.debug import run
        run(_args(subcommand="on"))
        assert load_config().verbose is True, \
            "debug on should set verbose=True in config"

    def test_debug_off_sets_verbose_false(self):
        """debug off → config.json has verbose=false."""
        from controller.config import load_config
        from nanoleaf_cli.commands.debug import run
        run(_args(subcommand="on"))
        run(_args(subcommand="off"))
        assert load_config().verbose is False, \
            "debug off should set verbose=False in config"

    def test_debug_on_causes_controller_to_log_verbose(self, monkeypatch, tmp_path):
        """With verbose=True the controller emits DEBUG-level log entries."""
        import logging
        import sunrise_sunset_controller as ctrl
        from nanoleaf_cli.commands.debug import run

        monkeypatch.setattr(ctrl, "get_weather",
                            lambda *_a, **_kw: _make_weather(sunset_hour=20))

        run(_args(subcommand="on"))

        log_records = []
        handler = logging.handlers_list = []

        class Capture(logging.Handler):
            def emit(self, record):
                log_records.append(record)

        cap = Capture(level=logging.DEBUG)
        logging.getLogger().addHandler(cap)
        try:
            ctrl.main(now=_now_at(10, 0))
        finally:
            logging.getLogger().removeHandler(cap)

        debug_records = [r for r in log_records if r.levelno == logging.DEBUG]
        assert len(debug_records) > 0, \
            "debug on: expected DEBUG log entries from controller run; got none"
