"""Unit tests for the nanoleaf-cli package.

Covers: validation layer, config/profile/party/debug/color-name commands,
formatting helpers, logs -n, status, and error commands.
Network (lamp) calls are always mocked; file I/O uses tmp_path.
"""

import argparse
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def ns(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace from keyword args."""
    obj = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(obj, k, v)
    return ns_defaults(obj)


def ns_defaults(obj: argparse.Namespace) -> argparse.Namespace:
    """Ensure common optional attrs exist on a Namespace."""
    for attr, default in [("verbose", False), ("n", 1)]:
        if not hasattr(obj, attr):
            setattr(obj, attr, default)
    return obj


def dt(hour, minute=0) -> datetime:
    return datetime(2024, 6, 15, hour, minute, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures — redirect config and state paths to tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect all CONFIG_PATH references to a temp file and clear cache."""
    import controller.config as _cfg
    import nanoleaf_cli.commands.config as _cmd_cfg
    import nanoleaf_cli.commands.profile as _cmd_prof
    import nanoleaf_cli.commands.debug as _cmd_dbg

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(_cmd_cfg, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(_cmd_prof, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(_cmd_dbg, "CONFIG_PATH", cfg_path)
    _cfg._config_cache.clear()
    yield cfg_path
    _cfg._config_cache.clear()


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect STATE_PATH and STATE_DIR to a temp directory."""
    import controller.state as _state
    monkeypatch.setattr(_state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(_state, "STATE_PATH", tmp_path / "state.json")
    return tmp_path / "state.json"


# ---------------------------------------------------------------------------
# Validation layer
# ---------------------------------------------------------------------------

class TestValidation:
    def test_hue_valid(self):
        from nanoleaf_cli._validation import validate_hue
        assert validate_hue("0") == 0
        assert validate_hue("180") == 180
        assert validate_hue("360") == 360

    def test_hue_out_of_range(self):
        from nanoleaf_cli._validation import validate_hue
        with pytest.raises(argparse.ArgumentTypeError):
            validate_hue("361")
        with pytest.raises(argparse.ArgumentTypeError):
            validate_hue("-1")

    def test_hue_non_integer(self):
        from nanoleaf_cli._validation import validate_hue
        with pytest.raises(argparse.ArgumentTypeError):
            validate_hue("red")

    def test_saturation_valid(self):
        from nanoleaf_cli._validation import validate_saturation
        assert validate_saturation("0") == 0
        assert validate_saturation("100") == 100

    def test_saturation_out_of_range(self):
        from nanoleaf_cli._validation import validate_saturation
        with pytest.raises(argparse.ArgumentTypeError):
            validate_saturation("101")

    def test_brightness_valid(self):
        from nanoleaf_cli._validation import validate_brightness
        assert validate_brightness("50") == 50

    def test_color_temp_valid(self):
        from nanoleaf_cli._validation import validate_color_temp
        assert validate_color_temp("1200") == 1200
        assert validate_color_temp("6500") == 6500

    def test_color_temp_out_of_range(self):
        from nanoleaf_cli._validation import validate_color_temp
        with pytest.raises(argparse.ArgumentTypeError):
            validate_color_temp("1199")
        with pytest.raises(argparse.ArgumentTypeError):
            validate_color_temp("6501")

    def test_time_str_valid(self):
        from nanoleaf_cli._validation import validate_time_str
        assert validate_time_str("06:00") == "06:00"
        assert validate_time_str("23:59") == "23:59"
        assert validate_time_str("09:05") == "09:05"

    def test_time_str_invalid(self):
        from nanoleaf_cli._validation import validate_time_str
        with pytest.raises(argparse.ArgumentTypeError):
            validate_time_str("25:00")
        with pytest.raises(argparse.ArgumentTypeError):
            validate_time_str("6am")
        with pytest.raises(argparse.ArgumentTypeError):
            validate_time_str("noon")

    def test_bool_valid(self):
        from nanoleaf_cli._validation import validate_bool
        for truthy in ("true", "True", "yes", "1"):
            assert validate_bool(truthy) is True, truthy
        for falsy in ("false", "False", "no", "0"):
            assert validate_bool(falsy) is False, falsy

    def test_bool_invalid(self):
        from nanoleaf_cli._validation import validate_bool
        with pytest.raises(argparse.ArgumentTypeError):
            validate_bool("maybe")

    def test_backoff_json(self):
        from nanoleaf_cli._validation import validate_backoff_schedule
        assert validate_backoff_schedule("[5, 10, 20]") == [5, 10, 20]

    def test_backoff_csv(self):
        from nanoleaf_cli._validation import validate_backoff_schedule
        assert validate_backoff_schedule("5,10,20,40") == [5, 10, 20, 40]

    def test_backoff_invalid_non_positive(self):
        from nanoleaf_cli._validation import validate_backoff_schedule
        with pytest.raises(argparse.ArgumentTypeError):
            validate_backoff_schedule("[5, -1, 20]")

    def test_backoff_invalid_format(self):
        from nanoleaf_cli._validation import validate_backoff_schedule
        with pytest.raises(argparse.ArgumentTypeError):
            validate_backoff_schedule("five,ten")

    def test_profile_name_valid(self):
        from nanoleaf_cli._validation import validate_profile_name
        assert validate_profile_name("night") == "NIGHT"
        assert validate_profile_name("PARTY") == "PARTY"
        assert validate_profile_name("sunrise_start") == "SUNRISE_START"

    def test_profile_name_invalid(self):
        from nanoleaf_cli._validation import validate_profile_name
        with pytest.raises(argparse.ArgumentTypeError):
            validate_profile_name("NEON")

    def test_validate_config_field_time(self):
        from nanoleaf_cli._validation import validate_config_field
        result = validate_config_field("morning_latest_start", "08:00")
        assert result == "08:00"

    def test_validate_config_field_bool(self):
        from nanoleaf_cli._validation import validate_config_field
        assert validate_config_field("verbose", "true") is True
        assert validate_config_field("verbose", "false") is False

    def test_validate_config_field_int(self):
        from nanoleaf_cli._validation import validate_config_field
        assert validate_config_field("adverse_offset_min", "45") == 45

    def test_validate_config_field_float(self):
        from nanoleaf_cli._validation import validate_config_field
        result = validate_config_field("dark_sun_elevation_deg", "15.5")
        assert result == 15.5

    def test_validate_config_field_backoff(self):
        from nanoleaf_cli._validation import validate_config_field
        assert validate_config_field("backoff_schedule_minutes", "5,10,20") == [5, 10, 20]

    def test_validate_config_field_unknown_key(self):
        from nanoleaf_cli._validation import validate_config_field
        with pytest.raises(argparse.ArgumentTypeError):
            validate_config_field("nonexistent_key", "value")

    def test_validate_profile_field_valid(self):
        from nanoleaf_cli._validation import validate_profile_field
        assert validate_profile_field("hue", "120") == 120
        assert validate_profile_field("mode", "ct") == "ct"
        assert validate_profile_field("color_temp", "4000") == 4000

    def test_validate_profile_field_invalid_field(self):
        from nanoleaf_cli._validation import validate_profile_field
        with pytest.raises(argparse.ArgumentTypeError):
            validate_profile_field("temperature", "5000")

    def test_validate_sun_elevation(self):
        from nanoleaf_cli._validation import validate_sun_elevation
        assert validate_sun_elevation("0") == 0.0
        assert validate_sun_elevation("-90") == -90.0
        assert validate_sun_elevation("90") == 90.0

    def test_validate_sun_elevation_out_of_range(self):
        from nanoleaf_cli._validation import validate_sun_elevation
        with pytest.raises(argparse.ArgumentTypeError):
            validate_sun_elevation("91")
        with pytest.raises(argparse.ArgumentTypeError):
            validate_sun_elevation("-91")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_fmt_time_shows_both_formats(self):
        from nanoleaf_cli._formatting import fmt_time
        result = fmt_time(time(6, 0))
        assert "06:00" in result
        assert "6:00" in result
        assert "AM" in result

    def test_fmt_time_pm(self):
        from nanoleaf_cli._formatting import fmt_time
        result = fmt_time(time(22, 30))
        assert "22:30" in result
        assert "PM" in result

    def test_fmt_profile_hsb(self):
        from nanoleaf_cli._formatting import fmt_profile
        from controller.config import LightProfile
        p = LightProfile(mode="hsb", hue=15, saturation=80, brightness=20)
        assert fmt_profile(p) == "HSB(15, 80, 20)"

    def test_fmt_profile_ct(self):
        from nanoleaf_cli._formatting import fmt_profile
        from controller.config import LightProfile
        p = LightProfile(mode="ct", color_temp=6000, brightness=100)
        assert fmt_profile(p) == "CT(6000, 100)"

    def test_confirm_config_set_output(self, capsys):
        from nanoleaf_cli._formatting import confirm_config_set
        confirm_config_set("morning_latest_start", "08:00")
        out = capsys.readouterr().out
        assert "morning_latest_start" in out
        assert "08:00" in out

    def test_confirm_config_set_verbose_shows_prev(self, capsys):
        from nanoleaf_cli._formatting import confirm_config_set
        confirm_config_set("adverse_offset_min", 45, prev=30, verbose=True)
        out = capsys.readouterr().out
        assert "30" in out

    def test_confirm_profile_set_output(self, capsys):
        from nanoleaf_cli._formatting import confirm_profile_set
        from controller.config import LightProfile
        p = LightProfile(mode="hsb", hue=20, saturation=80, brightness=20)
        confirm_profile_set("NIGHT", p)
        out = capsys.readouterr().out
        assert "NIGHT" in out
        assert "HSB(20, 80, 20)" in out

    def test_confirm_party_output(self, capsys):
        from nanoleaf_cli._formatting import confirm_party
        from controller.config import LightProfile
        p = LightProfile(mode="hsb", hue=280, saturation=90, brightness=100)
        ends = datetime(2024, 6, 15, 2, 0, tzinfo=UTC)
        confirm_party(p, ends, 30)
        out = capsys.readouterr().out
        assert "Party mode ON" in out
        assert "02:00" in out
        assert "30" in out

    def test_print_error_exits(self):
        from nanoleaf_cli._formatting import print_error
        with pytest.raises(SystemExit) as exc_info:
            print_error("something went wrong")
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------

class TestConfigCommands:
    def test_config_list_shows_all_keys(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_list
        run_list(ns(verbose=False))
        out = capsys.readouterr().out
        for key in ("morning_latest_start", "hard_cutoff_time", "verbose", "backoff_schedule_minutes"):
            assert key in out, f"expected {key!r} in config list output"

    def test_config_list_shows_formatted_time(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_list
        run_list(ns(verbose=False))
        out = capsys.readouterr().out
        assert "06:00" in out and "AM" in out

    def test_config_set_and_get_round_trip(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set, run_get
        run_set(ns(key="morning_latest_start", value="08:00", verbose=False))
        capsys.readouterr()  # discard confirmation
        run_get(ns(key="morning_latest_start"))
        out = capsys.readouterr().out
        assert "08:00" in out

    def test_config_set_persists_to_file(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set
        run_set(ns(key="adverse_offset_min", value="45", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert raw["adverse_offset_min"] == 45

    def test_config_set_verbose_shows_prev(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set
        run_set(ns(key="adverse_offset_min", value="30", verbose=False))
        capsys.readouterr()
        run_set(ns(key="adverse_offset_min", value="45", verbose=True))
        out = capsys.readouterr().out
        assert "30" in out  # previous value visible

    def test_config_reset_removes_key(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set, run_reset
        run_set(ns(key="adverse_offset_min", value="45", verbose=False))
        capsys.readouterr()
        run_reset(ns(key="adverse_offset_min", all=False))
        raw = json.loads(tmp_config.read_text())
        assert "adverse_offset_min" not in raw

    def test_config_reset_already_at_default(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_reset
        run_reset(ns(key="adverse_offset_min", all=False))
        out = capsys.readouterr().out
        assert "default" in out.lower()

    def test_config_reset_all_wipes_config(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set, run_reset
        run_set(ns(key="adverse_offset_min", value="45", verbose=False))
        capsys.readouterr()
        run_reset(ns(key=None, all=True))
        raw = json.loads(tmp_config.read_text())
        assert raw == {}

    def test_config_get_unknown_key_exits(self, tmp_config):
        from nanoleaf_cli.commands.config import run_get
        with pytest.raises(SystemExit):
            run_get(ns(key="totally_unknown_key"))

    def test_config_set_validates_input(self, tmp_config):
        from nanoleaf_cli.commands.config import run_set
        with pytest.raises(SystemExit):
            run_set(ns(key="morning_latest_start", value="99:99", verbose=False))

    def test_config_set_backoff_schedule(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set
        run_set(ns(key="backoff_schedule_minutes", value="5,10,30", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert raw["backoff_schedule_minutes"] == [5, 10, 30]


# ---------------------------------------------------------------------------
# Profile commands
# ---------------------------------------------------------------------------

class TestProfileCommands:
    def test_profile_list_shows_all(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_list
        run_list(ns(verbose=False))
        out = capsys.readouterr().out
        for name in ("NIGHT", "MORNING", "PARTY", "SUNRISE_START"):
            assert name in out

    def test_profile_get_valid(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_get
        run_get(ns(name="NIGHT", verbose=False))
        out = capsys.readouterr().out
        assert "NIGHT" in out
        assert "HSB" in out

    def test_profile_get_case_insensitive(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_get
        run_get(ns(name="night", verbose=False))
        out = capsys.readouterr().out
        assert "NIGHT" in out

    def test_profile_get_invalid_exits(self, tmp_config):
        from nanoleaf_cli.commands.profile import run_get
        with pytest.raises(SystemExit):
            run_get(ns(name="RAINBOW", verbose=False))

    def test_profile_set_changes_only_target_field(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_set
        from controller.config import PROFILE_DEFAULTS
        default_night = PROFILE_DEFAULTS["NIGHT"]

        run_set(ns(name="NIGHT", field="hue", value="20", verbose=False))
        capsys.readouterr()

        raw = json.loads(tmp_config.read_text())
        stored = raw["profiles"]["NIGHT"]
        assert stored["hue"] == 20
        # Other fields not written — they'll fall back to defaults on load
        assert "saturation" not in stored
        assert "brightness" not in stored

    def test_profile_set_cumulative(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_set
        run_set(ns(name="NIGHT", field="hue", value="20", verbose=False))
        run_set(ns(name="NIGHT", field="brightness", value="30", verbose=False))
        capsys.readouterr()
        raw = json.loads(tmp_config.read_text())
        stored = raw["profiles"]["NIGHT"]
        assert stored["hue"] == 20
        assert stored["brightness"] == 30

    def test_profile_set_confirmation_shows_effective_profile(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_set
        run_set(ns(name="NIGHT", field="hue", value="20", verbose=False))
        out = capsys.readouterr().out
        assert "NIGHT" in out
        assert "HSB(20," in out  # effective hue shown

    def test_profile_reset_removes_override(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_set, run_reset
        run_set(ns(name="NIGHT", field="hue", value="20", verbose=False))
        capsys.readouterr()
        run_reset(ns(name="NIGHT", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert "profiles" not in raw or "NIGHT" not in raw.get("profiles", {})

    def test_profile_reset_already_default(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_reset
        run_reset(ns(name="NIGHT", verbose=False))
        out = capsys.readouterr().out
        assert "default" in out.lower()


# ---------------------------------------------------------------------------
# Config color-name command
# ---------------------------------------------------------------------------

class TestColorName:
    def test_color_name_hex(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_color_name
        run_color_name(ns(hex="FF0000", rgb=None, cmyk=None, name="cherry red", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert any("red" in v.lower() or "cherry" in v.lower() for v in raw["color_names"].values())

    def test_color_name_rgb(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_color_name
        run_color_name(ns(hex=None, rgb="0,0,255", cmyk=None, name="pure blue", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert "pure blue" in raw["color_names"].values()

    def test_color_name_cmyk(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_color_name
        # CMYK (0,100,100,0) → red
        run_color_name(ns(hex=None, rgb=None, cmyk="0,100,100,0", name="signal red", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert "signal red" in raw["color_names"].values()

    def test_color_name_confirmation(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_color_name
        run_color_name(ns(hex="FF8000", rgb=None, cmyk=None, name="sunset orange", verbose=False))
        out = capsys.readouterr().out
        assert "sunset orange" in out
        assert "hue" in out.lower() or "°" in out

    def test_color_name_invalid_hex(self, tmp_config):
        from nanoleaf_cli.commands.config import run_color_name
        with pytest.raises(SystemExit):
            run_color_name(ns(hex="GGGGGG", rgb=None, cmyk=None, name="bad", verbose=False))

    def test_color_name_invalid_rgb_channels(self, tmp_config):
        from nanoleaf_cli.commands.config import run_color_name
        with pytest.raises(SystemExit):
            run_color_name(ns(hex=None, rgb="300,0,0", cmyk=None, name="bad", verbose=False))


# ---------------------------------------------------------------------------
# Debug commands
# ---------------------------------------------------------------------------

class TestDebugCommands:
    def test_debug_on_sets_verbose_true(self, tmp_config, capsys):
        from nanoleaf_cli.commands.debug import run_on
        run_on(ns())
        raw = json.loads(tmp_config.read_text())
        assert raw["verbose"] is True

    def test_debug_off_sets_verbose_false(self, tmp_config, capsys):
        from nanoleaf_cli.commands.debug import run_on, run_off
        run_on(ns())
        run_off(ns())
        raw = json.loads(tmp_config.read_text())
        assert raw["verbose"] is False

    def test_debug_on_prints_confirmation(self, tmp_config, capsys):
        from nanoleaf_cli.commands.debug import run_on
        run_on(ns())
        out = capsys.readouterr().out
        assert "verbose" in out.lower()

    def test_debug_off_prints_confirmation(self, tmp_config, capsys):
        from nanoleaf_cli.commands.debug import run_off
        run_off(ns())
        out = capsys.readouterr().out
        assert "verbose" in out.lower()


# ---------------------------------------------------------------------------
# Logs command
# ---------------------------------------------------------------------------

class TestLogsCommand:
    def test_logs_n_prints_last_n_lines(self, tmp_path, monkeypatch, capsys):
        import nanoleaf_cli.commands.logs as _logs_cmd
        log_file = tmp_path / "nanoleaf.log"
        lines = [f"line {i}\n" for i in range(20)]
        log_file.write_text("".join(lines))
        monkeypatch.setattr(_logs_cmd, "LOG_PATH", log_file)

        from nanoleaf_cli.commands.logs import run
        run(ns(n=5))
        out = capsys.readouterr().out
        printed = out.strip().split("\n")
        assert len(printed) == 5
        assert "line 19" in printed[-1]

    def test_logs_missing_file_exits(self, tmp_path, monkeypatch, capsys):
        import nanoleaf_cli.commands.logs as _logs_cmd
        monkeypatch.setattr(_logs_cmd, "LOG_PATH", tmp_path / "nonexistent.log")
        from nanoleaf_cli.commands.logs import run
        with pytest.raises(SystemExit):
            run(ns(n=5))


# ---------------------------------------------------------------------------
# Party command
# ---------------------------------------------------------------------------

class TestPartyCommand:
    def _now(self):
        return datetime(2024, 6, 15, 20, 0, 0).astimezone()

    def test_party_start_writes_state(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        run(ns(action=None, until="02:00", hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())

        state = json.loads(tmp_state.read_text())
        pm = state["party_mode"]
        assert pm["active"] is True
        assert "02:" in pm["ends_at"] or pm["ends_at"].endswith(("T02:00:00", "T02:00:00+"))
        assert "ends_at" in pm

    def test_party_start_uses_default_end_when_no_until(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        run(ns(action=None, until=None, hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())

        state = json.loads(tmp_state.read_text())
        assert state["party_mode"]["active"] is True

    def test_party_start_custom_hue(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        run(ns(action=None, until=None, hue=120, sat=90, brightness=80,
               color=None, fade=None, fade_duration=None), now=self._now())

        state = json.loads(tmp_state.read_text())
        assert state["party_mode"]["profile"]["hue"] == 120

    def test_party_start_rgb_color(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        run(ns(action=None, until=None, hue=None, sat=None, brightness=None,
               color="255,0,0", fade=None, fade_duration=None), now=self._now())

        state = json.loads(tmp_state.read_text())
        assert state["party_mode"]["profile"]["mode"] == "hsb"
        assert state["party_mode"]["profile"]["brightness"] == 100  # pure red is full brightness

    def test_party_start_fade_duration(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        run(ns(action=None, until=None, hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=15), now=self._now())

        state = json.loads(tmp_state.read_text())
        assert state["party_mode"]["fade_minutes"] == 15

    def test_party_stop_clears_state(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        # start it first
        run(ns(action=None, until="02:00", hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())
        # then stop
        run(ns(action="stop", until=None, hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())

        state = json.loads(tmp_state.read_text())
        assert state["party_mode"]["active"] is False

    def test_party_stop_when_not_active(self, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        run(ns(action="stop", until=None, hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())
        out = capsys.readouterr().out
        assert "not active" in out

    def test_party_until_advances_to_tomorrow_if_past(self, tmp_state, monkeypatch):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.party import run
        # now=20:00, until=19:00 → should roll to next day
        run(ns(action=None, until="19:00", hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())

        state = json.loads(tmp_state.read_text())
        pm = state["party_mode"]
        assert pm["active"] is True
        # ends_at must be in the future relative to now=20:00
        from datetime import datetime as _dt
        ends_at = _dt.fromisoformat(pm["ends_at"])
        now_aware = self._now()
        assert ends_at > now_aware, "ends_at must be after now when --until is in the past"


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

class TestStatusCommand:
    def test_status_shows_phase(self, tmp_config, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.status import run
        now = dt(14, 0)
        run(ns(verbose=False), now=now)
        out = capsys.readouterr().out
        assert "phase" in out
        assert "day" in out

    def test_status_shows_no_weather_when_no_cache(self, tmp_config, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.status import run
        run(ns(verbose=False), now=dt(14, 0))
        out = capsys.readouterr().out
        assert "no data" in out

    def test_status_verbose_shows_paths(self, tmp_config, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        from nanoleaf_cli.commands.status import run
        run(ns(verbose=True), now=dt(14, 0))
        out = capsys.readouterr().out
        assert "state file" in out or "config file" in out

    def test_status_shows_last_error(self, tmp_config, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        # Write a state with last_error
        state = {
            **_state._empty_state(),
            "last_error": {
                "timestamp": "2024-06-15T14:00:00+00:00",
                "type": "NanoleafConnectionError",
                "message": "Connection failed",
            },
        }
        tmp_state.write_text(json.dumps(state))

        from nanoleaf_cli.commands.status import run
        run(ns(verbose=False), now=dt(14, 0))
        out = capsys.readouterr().out
        assert "NanoleafConnectionError" in out or "Connection failed" in out


# ---------------------------------------------------------------------------
# Error command
# ---------------------------------------------------------------------------

class TestErrorCommand:
    def test_error_shows_state_error(self, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        import nanoleaf_cli.commands.error as _error_cmd
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)
        monkeypatch.setattr(_error_cmd, "LOG_PATH", tmp_state.parent / "nanoleaf.log")

        state = {
            **_state._empty_state(),
            "last_error": {
                "timestamp": "2024-06-15T13:00:00+00:00",
                "type": "NanoleafConnectionError",
                "message": "host unreachable",
            },
        }
        tmp_state.write_text(json.dumps(state))

        from nanoleaf_cli.commands.error import run
        run(ns(n=1))
        out = capsys.readouterr().out
        assert "NanoleafConnectionError" in out
        assert "host unreachable" in out

    def test_error_no_state_error(self, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        import nanoleaf_cli.commands.error as _error_cmd
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)
        monkeypatch.setattr(_error_cmd, "LOG_PATH", tmp_state.parent / "nanoleaf.log")

        from nanoleaf_cli.commands.error import run
        run(ns(n=1))
        out = capsys.readouterr().out
        assert "no errors" in out.lower()

    def test_error_shows_log_error_lines(self, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        import nanoleaf_cli.commands.error as _error_cmd
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        log_file = tmp_state.parent / "nanoleaf.log"
        log_file.write_text(
            "[2024-06-15 12:00:00] INFO foo: something\n"
            "[2024-06-15 13:00:00] ERROR bar: boom\n"
            "[2024-06-15 14:00:00] INFO foo: fine\n"
            "[2024-06-15 14:30:00] ERROR bar: second error\n"
        )
        monkeypatch.setattr(_error_cmd, "LOG_PATH", log_file)

        from nanoleaf_cli.commands.error import run
        run(ns(n=2))
        out = capsys.readouterr().out
        assert "boom" in out
        assert "second error" in out

    def test_error_n_limits_log_lines(self, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        import nanoleaf_cli.commands.error as _error_cmd
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

        log_file = tmp_state.parent / "nanoleaf.log"
        log_file.write_text(
            "".join(f"[2024-06-15 {i:02d}:00:00] ERROR bar: error {i}\n" for i in range(10))
        )
        monkeypatch.setattr(_error_cmd, "LOG_PATH", log_file)

        from nanoleaf_cli.commands.error import run
        run(ns(n=3))
        out = capsys.readouterr().out
        assert "error 9" in out
        assert "error 0" not in out  # only last 3
