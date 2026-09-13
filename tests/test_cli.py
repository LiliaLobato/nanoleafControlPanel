"""Unit tests for the nanoleaf-cli package.

Covers: validation layer, config/profile/party/debug/color-name commands,
formatting helpers, logs -n, status, and error commands.
Network (lamp) calls are always mocked; file I/O uses tmp_path.
"""

import argparse
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def ns(**kwargs) -> argparse.Namespace:
    """Build a Namespace with common optional attrs defaulted."""
    obj = argparse.Namespace(**kwargs)
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
    """Redirect CONFIG_PATH to a temp file and clear the mtime cache.

    Patching controller.config.CONFIG_PATH is sufficient: _config_io reads
    CONFIG_PATH through the module reference, and load_config/save_config/
    load_profiles all go through controller.config.CONFIG_PATH directly.
    """
    import controller.config as _cfg
    import nanoleaf_cli._config_io as _config_io

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(_config_io, "_cfg_module", _cfg)
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
# Validation — range checks, formats, dispatch
# ---------------------------------------------------------------------------

class TestValidation:
    def test_numeric_ranges(self):
        """Each validator accepts its full valid range and rejects one step beyond each boundary."""
        from nanoleaf_cli._validation import (
            validate_hue, validate_saturation, validate_brightness, validate_color_temp,
        )
        cases = [
            (validate_hue,         "0",    0,    "hue 0 is valid"),
            (validate_hue,         "359",  359,  "hue 359 is valid"),
            (validate_saturation,  "0",    0,    "saturation 0 is valid"),
            (validate_saturation,  "100",  100,  "saturation 100 is valid"),
            (validate_brightness,  "50",   50,   "brightness 50 is valid"),
            (validate_color_temp,  "1200", 1200, "color_temp 1200 is valid"),
            (validate_color_temp,  "6500", 6500, "color_temp 6500 is valid"),
        ]
        for fn, val, expected, msg in cases:
            assert fn(val) == expected, msg

        out_of_range = [
            (validate_hue,        "360",  "hue 360 should be rejected"),
            (validate_hue,        "361",  "hue 361 should be rejected"),
            (validate_hue,        "-1",   "hue -1 should be rejected"),
            (validate_saturation, "101",  "saturation 101 should be rejected"),
            (validate_color_temp, "1199", "color_temp 1199 should be rejected"),
            (validate_color_temp, "6501", "color_temp 6501 should be rejected"),
            (validate_hue,        "red",  "non-integer hue should be rejected"),
        ]
        for fn, val, msg in out_of_range:
            with pytest.raises(argparse.ArgumentTypeError):
                fn(val)

    def test_time_and_bool(self):
        """validate_time_str accepts HH:MM and rejects bad formats; validate_bool covers all tokens."""
        from nanoleaf_cli._validation import validate_time_str, validate_bool

        assert validate_time_str("06:00") == "06:00", "06:00 should round-trip"
        assert validate_time_str("23:59") == "23:59", "23:59 should round-trip"

        for bad in ("25:00", "6am", "noon"):
            with pytest.raises(argparse.ArgumentTypeError):
                validate_time_str(bad), f"{bad!r} should be rejected by validate_time_str"

        for truthy in ("true", "True", "yes", "1"):
            assert validate_bool(truthy) is True, f"{truthy!r} should map to True"
        for falsy in ("false", "False", "no", "0"):
            assert validate_bool(falsy) is False, f"{falsy!r} should map to False"
        with pytest.raises(argparse.ArgumentTypeError):
            validate_bool("maybe"), "unrecognised token should be rejected"

    def test_backoff_schedule(self):
        """Accepts JSON array or comma-separated ints; rejects non-positive values."""
        from nanoleaf_cli._validation import validate_backoff_schedule
        assert validate_backoff_schedule("[5, 10, 20]") == [5, 10, 20], "JSON array should parse"
        assert validate_backoff_schedule("5,10,20,40") == [5, 10, 20, 40], "CSV should parse"
        with pytest.raises(argparse.ArgumentTypeError):
            validate_backoff_schedule("[5, -1, 20]"), "negative value should be rejected"
        with pytest.raises(argparse.ArgumentTypeError):
            validate_backoff_schedule("five,ten"), "non-integer CSV should be rejected"

    def test_profile_name(self):
        """Normalises to uppercase; rejects unknown names."""
        from nanoleaf_cli._validation import validate_profile_name
        assert validate_profile_name("night") == "NIGHT", "lowercase should be normalised"
        assert validate_profile_name("sunrise_start") == "SUNRISE_START", "underscore name normalised"
        with pytest.raises(argparse.ArgumentTypeError):
            validate_profile_name("NEON"), "unknown profile name should be rejected"

    def test_config_field_dispatch(self):
        """validate_config_field routes each Config field type to the right validator."""
        from nanoleaf_cli._validation import validate_config_field
        assert validate_config_field("morning_latest_start", "08:00") == "08:00", "time field"
        assert validate_config_field("verbose", "true") is True,  "bool field → True"
        assert validate_config_field("verbose", "false") is False, "bool field → False"
        assert validate_config_field("adverse_offset_min", "45") == 45, "int field"
        assert validate_config_field("dark_sun_elevation_deg", "15.5") == 15.5, "float field"
        assert validate_config_field("backoff_schedule_minutes", "5,10,20") == [5, 10, 20], "list field"
        with pytest.raises(argparse.ArgumentTypeError):
            validate_config_field("nonexistent_key", "value"), "unknown key should be rejected"

    def test_profile_field_dispatch(self):
        """validate_profile_field routes each LightProfile field; rejects unknown field names."""
        from nanoleaf_cli._validation import validate_profile_field
        assert validate_profile_field("hue", "120") == 120, "hue field"
        assert validate_profile_field("mode", "ct") == "ct", "mode field"
        assert validate_profile_field("color_temp", "4000") == 4000, "color_temp field"
        with pytest.raises(argparse.ArgumentTypeError):
            validate_profile_field("temperature", "5000"), "unknown field name should be rejected"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_fmt_time_and_profile(self):
        """fmt_time shows both 24 h and 12 h; fmt_profile shows correct mode prefix."""
        from nanoleaf_cli._formatting import fmt_time, fmt_profile
        from controller.config import LightProfile

        am = fmt_time(time(6, 0))
        assert "06:00" in am and "AM" in am, f"fmt_time(06:00) should contain 24h and AM, got {am!r}"
        pm = fmt_time(time(22, 30))
        assert "22:30" in pm and "PM" in pm, f"fmt_time(22:30) should contain 24h and PM, got {pm!r}"

        assert fmt_profile(LightProfile(mode="hsb", hue=15, saturation=80, brightness=20)) == "HSB(15, 80, 20)"
        assert fmt_profile(LightProfile(mode="ct", color_temp=6000, brightness=100)) == "CT(6000, 100)"

    def test_confirm_helpers_output(self, capsys):
        """confirm_config_set and confirm_profile_set include key/name and value; verbose shows prev."""
        from nanoleaf_cli._formatting import confirm_config_set, confirm_profile_set, confirm_party
        from controller.config import LightProfile

        confirm_config_set("morning_latest_start", "08:00 (8:00 AM)")
        out = capsys.readouterr().out
        assert "morning_latest_start" in out and "08:00" in out, \
            f"confirm_config_set should show key and value, got {out!r}"

        confirm_config_set("adverse_offset_min", "45", prev=30, verbose=True)
        out = capsys.readouterr().out
        assert "30" in out, f"verbose mode should show previous value 30, got {out!r}"

        p = LightProfile(mode="hsb", hue=20, saturation=80, brightness=20)
        confirm_profile_set("NIGHT", p)
        out = capsys.readouterr().out
        assert "NIGHT" in out and "HSB(20, 80, 20)" in out, \
            f"confirm_profile_set should show name and profile, got {out!r}"

        confirm_party(p, datetime(2024, 6, 15, 2, 0, tzinfo=UTC), 30)
        out = capsys.readouterr().out
        assert "Party mode ON" in out and "02:00" in out and "30" in out, \
            f"confirm_party should show mode, end time, and fade, got {out!r}"

    def test_print_error_exits_with_code_1(self, capsys):
        from nanoleaf_cli._formatting import print_error
        with pytest.raises(SystemExit) as exc_info:
            print_error("something went wrong")
        assert exc_info.value.code == 1, "print_error should exit with code 1"
        assert "something went wrong" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------

class TestConfigCommands:
    def test_list_shows_all_keys_with_formatted_times(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_list
        run_list(ns(verbose=False))
        out = capsys.readouterr().out
        for key in ("morning_latest_start", "hard_cutoff_time", "verbose", "backoff_schedule_minutes"):
            assert key in out, f"config list should include {key!r}"
        assert "06:00" in out and "AM" in out, "time values should be shown in HH:MM + 12h format"

    def test_set_get_round_trip_and_file_persistence(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set, run_get
        run_set(ns(key="adverse_offset_min", value="45", verbose=False))
        capsys.readouterr()

        raw = json.loads(tmp_config.read_text())
        assert raw["adverse_offset_min"] == 45, "config set should write int value to config.json"

        run_get(ns(key="morning_latest_start"))
        capsys.readouterr()  # default, no file change needed

        run_set(ns(key="morning_latest_start", value="08:00", verbose=False))
        capsys.readouterr()
        run_get(ns(key="morning_latest_start"))
        out = capsys.readouterr().out
        assert "08:00" in out, f"config get should return the value just set, got {out!r}"

    def test_set_verbose_shows_previous_value(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set
        run_set(ns(key="adverse_offset_min", value="30", verbose=False))
        capsys.readouterr()
        run_set(ns(key="adverse_offset_min", value="45", verbose=True))
        out = capsys.readouterr().out
        assert "30" in out, f"verbose set should show previous value 30, got {out!r}"

    def test_reset_removes_key_and_reset_all_wipes_config(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set, run_reset

        run_set(ns(key="adverse_offset_min", value="45", verbose=False))
        capsys.readouterr()
        run_reset(ns(key="adverse_offset_min", all=False))
        raw = json.loads(tmp_config.read_text())
        assert "adverse_offset_min" not in raw, \
            "reset should remove the key from config.json"

        run_reset(ns(key="adverse_offset_min", all=False))
        out = capsys.readouterr().out
        assert "default" in out.lower(), \
            f"resetting an unset key should mention 'default', got {out!r}"

        run_set(ns(key="adverse_offset_min", value="45", verbose=False))
        capsys.readouterr()
        run_reset(ns(key=None, all=True))
        assert json.loads(tmp_config.read_text()) == {}, \
            "reset --all should wipe config.json to an empty object"

    def test_invalid_inputs_exit(self, tmp_config):
        from nanoleaf_cli.commands.config import run_get, run_set
        with pytest.raises(SystemExit):
            run_get(ns(key="totally_unknown_key")), "get with unknown key should exit"
        with pytest.raises(SystemExit):
            run_set(ns(key="morning_latest_start", value="99:99", verbose=False)), \
                "set with invalid time format should exit"

    def test_set_backoff_schedule_parses_csv(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_set
        run_set(ns(key="backoff_schedule_minutes", value="5,10,30", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert raw["backoff_schedule_minutes"] == [5, 10, 30], \
            "backoff schedule CSV should be stored as a list of ints"


# ---------------------------------------------------------------------------
# Profile commands
# ---------------------------------------------------------------------------

class TestProfileCommands:
    def test_list_and_get(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_list, run_get

        run_list(ns(verbose=False))
        out = capsys.readouterr().out
        for name in ("NIGHT", "MORNING", "PARTY", "SUNRISE_START"):
            assert name in out, f"profile list should include {name!r}"

        run_get(ns(name="night", verbose=False))  # lowercase input
        out = capsys.readouterr().out
        assert "NIGHT" in out and "HSB" in out, \
            f"profile get should show normalised name and mode, got {out!r}"

        with pytest.raises(SystemExit):
            run_get(ns(name="RAINBOW", verbose=False)), "unknown profile name should exit"

    def test_set_only_changes_specified_field(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_set
        run_set(ns(name="NIGHT", field="hue", value="20", verbose=False))
        out = capsys.readouterr().out

        raw = json.loads(tmp_config.read_text())
        stored = raw["profiles"]["NIGHT"]
        assert stored["hue"] == 20, "set should write hue=20 to config.json"
        assert "saturation" not in stored, "set hue should NOT write saturation (field-level merge)"
        assert "brightness" not in stored, "set hue should NOT write brightness (field-level merge)"
        assert "NIGHT" in out and "HSB(20," in out, \
            f"confirmation should show effective profile with new hue, got {out!r}"

        run_set(ns(name="NIGHT", field="brightness", value="30", verbose=False))
        capsys.readouterr()
        raw = json.loads(tmp_config.read_text())
        assert raw["profiles"]["NIGHT"]["hue"] == 20, "previous hue should be preserved after second set"
        assert raw["profiles"]["NIGHT"]["brightness"] == 30, "new brightness should be written"

    def test_reset_removes_override(self, tmp_config, capsys):
        from nanoleaf_cli.commands.profile import run_set, run_reset
        run_set(ns(name="NIGHT", field="hue", value="20", verbose=False))
        capsys.readouterr()
        run_reset(ns(name="NIGHT", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert "NIGHT" not in raw.get("profiles", {}), \
            "reset should remove the profile override from config.json"

        run_reset(ns(name="NIGHT", verbose=False))
        out = capsys.readouterr().out
        assert "default" in out.lower(), \
            f"resetting a profile with no override should say 'default', got {out!r}"


# ---------------------------------------------------------------------------
# Config color-name command
# ---------------------------------------------------------------------------

class TestColorName:
    def test_color_sources_write_to_config(self, tmp_config, capsys):
        from nanoleaf_cli.commands.config import run_color_name

        run_color_name(ns(hex="FF0000", rgb=None, cmyk=None, name="cherry red", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert any("cherry red" in v for v in raw["color_names"].values()), \
            "hex source should write name to color_names"

        run_color_name(ns(hex=None, rgb="0,0,255", cmyk=None, name="pure blue", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert "pure blue" in raw["color_names"].values(), "rgb source should write name"

        run_color_name(ns(hex=None, rgb=None, cmyk="0,100,100,0", name="signal red", verbose=False))
        raw = json.loads(tmp_config.read_text())
        assert "signal red" in raw["color_names"].values(), "cmyk source should write name"

        run_color_name(ns(hex="FF8000", rgb=None, cmyk=None, name="sunset orange", verbose=False))
        out = capsys.readouterr().out
        assert "sunset orange" in out and ("°" in out or "hue" in out.lower()), \
            f"confirmation should include name and hue info, got {out!r}"

    def test_invalid_inputs_exit(self, tmp_config):
        from nanoleaf_cli.commands.config import run_color_name
        with pytest.raises(SystemExit):
            run_color_name(ns(hex="GGGGGG", rgb=None, cmyk=None, name="bad", verbose=False)), \
                "invalid hex chars should exit"
        with pytest.raises(SystemExit):
            run_color_name(ns(hex=None, rgb="300,0,0", cmyk=None, name="bad", verbose=False)), \
                "out-of-range RGB channel should exit"


# ---------------------------------------------------------------------------
# Debug commands
# ---------------------------------------------------------------------------

class TestDebugCommands:
    def test_debug_on_off_toggles_verbose_in_config(self, tmp_config, capsys):
        from nanoleaf_cli.commands.debug import run_on, run_off

        run_on(ns())
        raw = json.loads(tmp_config.read_text())
        assert raw["verbose"] is True, "debug on should write verbose=true to config.json"
        out = capsys.readouterr().out
        assert "verbose" in out.lower(), f"debug on should confirm, got {out!r}"

        run_off(ns())
        raw = json.loads(tmp_config.read_text())
        assert raw["verbose"] is False, "debug off should write verbose=false to config.json"
        out = capsys.readouterr().out
        assert "verbose" in out.lower(), f"debug off should confirm, got {out!r}"


# ---------------------------------------------------------------------------
# Logs command
# ---------------------------------------------------------------------------

class TestLogsCommand:
    def test_n_prints_last_n_lines_and_missing_file_exits(self, tmp_path, monkeypatch, capsys):
        import nanoleaf_cli.commands.logs as _logs_cmd

        log_file = tmp_path / "nanoleaf.log"
        log_file.write_text("".join(f"line {i}\n" for i in range(20)))
        monkeypatch.setattr(_logs_cmd, "LOG_PATH", log_file)

        from nanoleaf_cli.commands.logs import run
        run(ns(n=5))
        out = capsys.readouterr().out
        printed = out.strip().split("\n")
        assert len(printed) == 5, f"expected 5 lines, got {len(printed)}"
        assert "line 19" in printed[-1], f"last line should be 'line 19', got {printed[-1]!r}"

        monkeypatch.setattr(_logs_cmd, "LOG_PATH", tmp_path / "nonexistent.log")
        with pytest.raises(SystemExit):
            run(ns(n=5)), "missing log file should exit"


# ---------------------------------------------------------------------------
# Party command
# ---------------------------------------------------------------------------

class TestPartyCommand:
    def _now(self):
        return datetime(2024, 6, 15, 20, 0, 0).astimezone()

    def _patch_state(self, monkeypatch, tmp_state):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)

    def test_start_writes_active_party_with_options(self, tmp_state, monkeypatch):
        self._patch_state(monkeypatch, tmp_state)
        from nanoleaf_cli.commands.party import run

        run(ns(action=None, until="02:00", hue=120, sat=90, brightness=80,
               color=None, fade=None, fade_duration=15), now=self._now())

        pm = json.loads(tmp_state.read_text())["party_mode"]
        assert pm["active"] is True, "party_mode.active should be True after start"
        assert "02:" in pm["ends_at"], f"ends_at should contain '02:', got {pm['ends_at']!r}"
        assert pm["profile"]["hue"] == 120, f"custom hue should be stored, got {pm['profile']['hue']}"
        assert pm["fade_minutes"] == 15, f"fade_duration should be stored as fade_minutes, got {pm['fade_minutes']}"

    def test_start_rgb_color_converts_to_hsb(self, tmp_state, monkeypatch):
        self._patch_state(monkeypatch, tmp_state)
        from nanoleaf_cli.commands.party import run

        run(ns(action=None, until=None, hue=None, sat=None, brightness=None,
               color="255,0,0", fade=None, fade_duration=None), now=self._now())

        prof = json.loads(tmp_state.read_text())["party_mode"]["profile"]
        assert prof["mode"] == "hsb", "RGB color should produce mode=hsb"
        assert prof["brightness"] == 100, "pure red (255,0,0) has full brightness"

    def test_stop_clears_party_and_handles_already_stopped(self, tmp_state, monkeypatch, capsys):
        self._patch_state(monkeypatch, tmp_state)
        from nanoleaf_cli.commands.party import run

        run(ns(action=None, until="02:00", hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())
        run(ns(action="stop", until=None, hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())

        assert json.loads(tmp_state.read_text())["party_mode"]["active"] is False, \
            "stop should set party_mode.active to False"

        run(ns(action="stop", until=None, hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())
        out = capsys.readouterr().out
        assert "not active" in out, f"stopping when already stopped should say 'not active', got {out!r}"

    def test_until_rolls_to_tomorrow_when_time_is_past(self, tmp_state, monkeypatch):
        """--until 19:00 with now=20:00 must schedule ends_at on the next day."""
        self._patch_state(monkeypatch, tmp_state)
        from nanoleaf_cli.commands.party import run

        run(ns(action=None, until="19:00", hue=None, sat=None, brightness=None,
               color=None, fade=None, fade_duration=None), now=self._now())

        ends_at = datetime.fromisoformat(
            json.loads(tmp_state.read_text())["party_mode"]["ends_at"]
        )
        assert ends_at > self._now(), \
            f"ends_at {ends_at} should be after now {self._now()} when --until is already past"


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

class _FakeStatusLamp:
    """Fast stand-in for NanoleafLight in status tests — records get_full_state's
    retries kwarg and returns {} (unreachable) instantly, so tests never hit the
    network or the retry budget."""
    last_retries = None

    def __init__(self, *args, **kwargs):
        pass

    def get_full_state(self, retries=2, retry_delay=10.0, with_panels=False):
        _FakeStatusLamp.last_retries = retries
        return {}


class TestStatusCommand:
    def test_output_structure(self, tmp_config, tmp_state, monkeypatch, capsys):
        """Status shows phase + time + weather placeholder; verbose adds file paths."""
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)
        import nanoleaf_cli.commands.status as _status
        monkeypatch.setattr(_status, "NanoleafLight", _FakeStatusLamp)

        from nanoleaf_cli.commands.status import run

        run(ns(verbose=False), now=dt(14, 0))
        out = capsys.readouterr().out
        assert "phase" in out and "day" in out, \
            f"status at 14:00 should show phase=day, got {out!r}"
        assert "no data" in out, "no weather cache → should say 'no data'"

        run(ns(verbose=True), now=dt(14, 0))
        out = capsys.readouterr().out
        assert "state file" in out or "config file" in out, \
            f"verbose mode should show file paths, got {out!r}"

    def test_shows_last_error_from_state(self, tmp_config, tmp_state, monkeypatch, capsys):
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)
        import nanoleaf_cli.commands.status as _status
        monkeypatch.setattr(_status, "NanoleafLight", _FakeStatusLamp)

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
        assert "NanoleafConnectionError" in out or "Connection failed" in out, \
            f"status should surface last_error, got {out!r}"

    def test_status_uses_retries_zero_fast_fail(self, tmp_config, tmp_state, monkeypatch):
        """CLI status must call get_full_state(retries=0) so it fails fast instead
        of blocking on the controller's ~20s retry budget when the lamp is down."""
        import controller.state as _state
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)
        monkeypatch.setenv("NANOLEAF_IP_ADDRESS", "10.0.0.141")
        monkeypatch.setenv("NANOLEAF_AUTH_TOKEN", "tok")
        import nanoleaf_cli.commands.status as _status
        _FakeStatusLamp.last_retries = None
        monkeypatch.setattr(_status, "NanoleafLight", _FakeStatusLamp)

        from nanoleaf_cli.commands.status import run
        run(ns(verbose=False), now=dt(14, 0))
        assert _FakeStatusLamp.last_retries == 0, \
            "CLI status must call get_full_state(retries=0) for fast-fail"


# ---------------------------------------------------------------------------
# Error command
# ---------------------------------------------------------------------------

class TestErrorCommand:
    def _patch(self, monkeypatch, tmp_state, log_file=None):
        import controller.state as _state
        import nanoleaf_cli.commands.error as _error_cmd
        monkeypatch.setattr(_state, "STATE_DIR", tmp_state.parent)
        monkeypatch.setattr(_state, "STATE_PATH", tmp_state)
        if log_file:
            monkeypatch.setattr(_error_cmd, "LOG_PATH", log_file)
        else:
            monkeypatch.setattr(_error_cmd, "LOG_PATH", tmp_state.parent / "nanoleaf.log")

    def test_shows_state_error_and_no_error_message(self, tmp_state, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_state)
        import controller.state as _state
        from nanoleaf_cli.commands.error import run

        # No error in state
        run(ns(n=1))
        out = capsys.readouterr().out
        assert "no errors" in out.lower(), \
            f"empty state should print 'no errors', got {out!r}"

        # With error in state
        tmp_state.write_text(json.dumps({
            **_state._empty_state(),
            "last_error": {
                "timestamp": "2024-06-15T13:00:00+00:00",
                "type": "NanoleafConnectionError",
                "message": "host unreachable",
            },
        }))
        run(ns(n=1))
        out = capsys.readouterr().out
        assert "NanoleafConnectionError" in out and "host unreachable" in out, \
            f"state error should appear in output, got {out!r}"

    def test_log_error_lines_respect_n_limit(self, tmp_state, monkeypatch, capsys):
        """Shows last N ERROR log lines; non-ERROR lines are excluded."""
        log_file = tmp_state.parent / "nanoleaf.log"
        log_file.write_text(
            "[2024-06-15 12:00:00] INFO foo: something fine\n"
            + "".join(
                f"[2024-06-15 {i:02d}:00:00] ERROR bar: error {i}\n" for i in range(10)
            )
        )
        self._patch(monkeypatch, tmp_state, log_file)
        from nanoleaf_cli.commands.error import run

        run(ns(n=3))
        out = capsys.readouterr().out
        assert "error 9" in out, f"last error (9) should be shown, got {out!r}"
        assert "error 0" not in out, f"only last 3 errors shown; error 0 should be absent, got {out!r}"
        assert "something fine" not in out, f"INFO lines should be excluded, got {out!r}"
