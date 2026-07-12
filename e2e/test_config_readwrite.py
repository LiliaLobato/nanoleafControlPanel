"""Config and profile read-write end-to-end tests.

Exhaustively covers every config field and every profile field via the CLI
layer: set → verify get output → verify load_config()/load_profiles() reads
the same value → reset → verify default is restored.

No lamp required. Run with:
    pytest e2e/test_config_readwrite.py -v
"""

import argparse
import json
from datetime import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    """Redirect CONFIG_PATH to a temp file and clear the mtime cache each test.

    CONFIG_PATH lives in controller.config; load_config/save_config and
    _config_io.load_raw_config all resolve it via that module at call time, so
    patching the single controller.config global redirects every reader/writer.
    """
    import controller.config as cfg_mod

    tmp_cfg = tmp_path / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_cfg)
    cfg_mod._config_cache.clear()
    yield tmp_cfg
    cfg_mod._config_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(**kwargs):
    return argparse.Namespace(**kwargs)


def _set(key, value):
    from nanoleaf_cli.commands.config import run_set
    run_set(_args(key=key, value=str(value), verbose=False))


def _get_output(key, capsys):
    from nanoleaf_cli.commands.config import run_get
    capsys.readouterr()   # flush output from preceding calls
    run_get(_args(key=key))
    return capsys.readouterr().out.strip()


def _reset(key):
    from nanoleaf_cli.commands.config import run_reset
    run_reset(_args(key=key, all=False))


def _load_raw(tmp_cfg: Path) -> dict:
    if not tmp_cfg.exists():
        return {}
    return json.loads(tmp_cfg.read_text())


def _pset(profile, field, value):
    from nanoleaf_cli.commands.profile import run_set
    run_set(_args(name=profile, field=field, value=str(value), verbose=False))


def _preset(profile):
    from nanoleaf_cli.commands.profile import run_reset
    run_reset(_args(name=profile))


# ---------------------------------------------------------------------------
# CONFIG FIELDS
# ---------------------------------------------------------------------------

# (field, set_to, expected_get_substring, default_substring)
TIME_FIELDS = [
    ("morning_latest_start",       "07:00", "07:00", "06:00"),
    ("full_morning_time",          "07:30", "07:30", "07:00"),
    ("force_evening_time",         "21:30", "21:30", "21:00"),
    ("night_full_time",            "22:30", "22:30", "22:00"),
    ("hard_cutoff_time",           "23:30", "23:30", "23:00"),
    ("weather_fetch_night",        "00:30", "00:30", "00:00"),
    ("weather_fetch_morning",      "03:30", "03:30", "03:00"),
    ("weather_fetch_midday",       "09:30", "09:30", "09:00"),
    ("weather_fetch_evening",      "14:30", "14:30", "14:00"),
    ("weather_fetch_late_evening", "20:30", "20:30", "20:00"),
    ("party_default_end",          "01:30", "01:30", "02:00"),
]

INT_FIELDS = [
    # (field,                         set_to, stored, default)
    ("adverse_offset_min",           "25",  25,   30),
    ("adverse_offset_max",           "80",  80,   75),
    ("cloud_threshold",              "65",  65,   60),
    ("dark_cloud_threshold",         "70",  70,   75),
    ("day_toggle_lockout_minutes",   "45",  45,   30),
    ("late_night_fade_minutes",      "90",  90,  120),
    ("party_default_fade_minutes",   "45",  45,   30),
    ("weather_cache_max_age_hours",  "6",    6,    5),
]


class TestConfigTimeFields:
    """Every time field: set → get → load_config → overwrite → reset → default restored."""

    @pytest.mark.parametrize("field,value,get_has,default_has", TIME_FIELDS)
    def test_roundtrip(self, field, value, get_has, default_has, capsys, tmp_config):
        from controller.config import load_config
        h, m = map(int, value.split(":"))
        expected_time = time(h, m)

        # set and verify get output
        _set(field, value)
        out = _get_output(field, capsys)
        assert get_has in out, \
            f"config get {field} after set {value!r}: expected {get_has!r} in {out!r}"

        # load_config() reads the same value
        cfg = load_config()
        assert getattr(cfg, field) == expected_time, \
            f"load_config().{field} after set {value!r}: expected {expected_time}, got {getattr(cfg, field)}"

        # key is present in raw JSON
        assert field in _load_raw(tmp_config), \
            f"{field} should be present in config.json after set"

        # overwrite with a different value works
        _set(field, "00:01")
        assert _load_raw(tmp_config).get(field) == "00:01", \
            f"{field} should update to 00:01 after second set"

        # reset removes the override and restores default
        _set(field, value)
        _reset(field)
        assert field not in _load_raw(tmp_config), \
            f"{field} should be removed from config.json after reset"
        out_after_reset = _get_output(field, capsys)
        assert default_has in out_after_reset, \
            f"config get {field} after reset: expected default {default_has!r} in {out_after_reset!r}"


class TestConfigIntFields:
    """Every int field: set → stored as int → load_config → reset."""

    @pytest.mark.parametrize("field,value,stored,default", INT_FIELDS)
    def test_roundtrip(self, field, value, stored, default, tmp_config):
        from controller.config import load_config

        _set(field, value)
        raw = _load_raw(tmp_config)
        assert raw.get(field) == stored, \
            f"config.json[{field!r}] after set: expected {stored}, got {raw.get(field)}"
        assert getattr(load_config(), field) == stored, \
            f"load_config().{field} after set: expected {stored}, got {getattr(load_config(), field)}"

        _reset(field)
        assert field not in _load_raw(tmp_config), \
            f"{field} should be absent from config.json after reset"
        assert getattr(load_config(), field) == default, \
            f"load_config().{field} after reset: expected default {default}, got {getattr(load_config(), field)}"


class TestConfigScalarFields:
    """Float, bool, and backoff-list fields."""

    def test_dark_sun_elevation_deg(self, tmp_config):
        from controller.config import load_config
        _set("dark_sun_elevation_deg", "18.5")
        assert abs(load_config().dark_sun_elevation_deg - 18.5) < 0.01, \
            "dark_sun_elevation_deg should be 18.5 after set"
        _reset("dark_sun_elevation_deg")
        assert abs(load_config().dark_sun_elevation_deg - 35.0) < 0.01, \
            "dark_sun_elevation_deg should be default 35.0 after reset"

    def test_verbose_true_false_and_aliases(self):
        from controller.config import load_config
        for truthy in ("true", "1", "yes"):
            _reset("verbose")
            _set("verbose", truthy)
            assert load_config().verbose is True, \
                f"verbose should be True for value {truthy!r}"
        for falsy in ("false", "0", "no"):
            _set("verbose", "true")
            _set("verbose", falsy)
            assert load_config().verbose is False, \
                f"verbose should be False for value {falsy!r}"
        _set("verbose", "true")
        _reset("verbose")
        assert load_config().verbose is False, \
            "verbose should be default False after reset"

    def test_backoff_schedule_csv_json_single_and_reset(self, tmp_config):
        from controller.config import load_config

        # CSV
        _set("backoff_schedule_minutes", "10,20,30,60")
        assert load_config().backoff_schedule_minutes == [10, 20, 30, 60], \
            "CSV backoff should parse to list of ints"
        assert isinstance(_load_raw(tmp_config).get("backoff_schedule_minutes"), list), \
            "backoff_schedule_minutes should be stored as JSON array"

        # JSON array
        _set("backoff_schedule_minutes", "[5, 15, 30]")
        assert load_config().backoff_schedule_minutes == [5, 15, 30], \
            "JSON array backoff should parse correctly"

        # single value
        _set("backoff_schedule_minutes", "60")
        assert load_config().backoff_schedule_minutes == [60], \
            "Single value should become a one-element list"

        # reset
        _reset("backoff_schedule_minutes")
        assert load_config().backoff_schedule_minutes == [1, 2, 5, 10, 20, 40, 60], \
            "After reset, backoff_schedule_minutes should be default [1, 2, 5, 10, 20, 40, 60]"


class TestConfigListAndResetAll:
    """config list shows all keys; reset --all wipes file and restores every default."""

    def test_list_shows_all_keys_and_reflects_overrides(self, capsys):
        from controller.config import Config
        from dataclasses import fields
        from nanoleaf_cli.commands.config import run_list

        _set("force_evening_time", "21:30")
        _set("cloud_threshold", "65")
        capsys.readouterr()
        run_list(_args())
        out = capsys.readouterr().out

        for f in fields(Config):
            assert f.name in out, \
                f"config list should show field {f.name!r}"
        assert "21:30" in out, "config list should show overridden force_evening_time 21:30"
        assert "65" in out,    "config list should show overridden cloud_threshold 65"
        # default values present for un-overridden fields
        assert "06:00" in out, "config list should show default morning_latest_start 06:00"

    def test_reset_all_wipes_file_and_restores_all_defaults(self, tmp_config):
        from controller.config import Config, load_config
        from dataclasses import fields
        from nanoleaf_cli.commands.config import run_reset

        _set("force_evening_time", "21:30")
        _set("cloud_threshold", "65")
        _set("verbose", "true")
        run_reset(_args(key=None, all=True))

        assert _load_raw(tmp_config) == {}, \
            "config reset --all should leave an empty config.json"
        defaults = Config()
        cfg = load_config()
        for f in fields(Config):
            assert getattr(cfg, f.name) == getattr(defaults, f.name), \
                f"After reset --all, {f.name} should be default " \
                f"{getattr(defaults, f.name)!r}, got {getattr(cfg, f.name)!r}"

    def test_multiple_overrides_coexist(self, tmp_config):
        _set("force_evening_time", "21:30")
        _set("cloud_threshold", "65")
        _set("verbose", "true")
        raw = _load_raw(tmp_config)
        assert raw.get("force_evening_time") == "21:30", "force_evening_time missing"
        assert raw.get("cloud_threshold") == 65,         "cloud_threshold missing"
        assert raw.get("verbose") is True,               "verbose missing"


class TestConfigInvalidValues:
    """Invalid inputs exit non-zero without writing to config.json."""

    @pytest.mark.parametrize("field,bad_value,reason", [
        ("force_evening_time",      "25:00",  "invalid hour"),
        ("force_evening_time",      "21:99",  "invalid minute"),
        ("force_evening_time",      "notTime","not a time string"),
        ("cloud_threshold",         "-1",     "below 0"),
        ("cloud_threshold",         "101",    "above 100"),
        ("cloud_threshold",         "abc",    "not a number"),
        ("adverse_offset_min",      "-5",     "negative"),
        ("dark_sun_elevation_deg",  "91",     "above 90°"),
        ("dark_sun_elevation_deg",  "-91",    "below -90°"),
        ("verbose",                 "maybe",  "not a bool"),
        ("backoff_schedule_minutes","not,a,list", "non-integer elements"),
    ])
    def test_invalid_value_exits_without_writing(self, field, bad_value, reason, tmp_config):
        from nanoleaf_cli.commands.config import run_set
        with pytest.raises(SystemExit):
            run_set(_args(key=field, value=bad_value, verbose=False))
        assert field not in _load_raw(tmp_config), \
            f"Invalid {field}={bad_value!r} ({reason}) must not be written to config.json"

    def test_unknown_key_does_not_write(self, tmp_config):
        from nanoleaf_cli.commands.config import run_set
        with pytest.raises((SystemExit, Exception)):
            run_set(_args(key="nonexistent_key", value="42", verbose=False))
        assert _load_raw(tmp_config).get("nonexistent_key") is None, \
            "Unknown config key should not be written to config.json"


# ---------------------------------------------------------------------------
# PROFILE FIELDS
# ---------------------------------------------------------------------------

class TestProfileHSBFields:
    """Set/get/reset for HSB fields across multiple profiles."""

    HSB_CASES = [
        ("NIGHT",        "hue",        20),
        ("NIGHT",        "saturation", 90),
        ("NIGHT",        "brightness", 30),
        ("PARTY",        "hue",        120),
        ("PARTY",        "saturation", 70),
        ("PARTY",        "brightness", 80),
        ("DAYTIME_ON",   "hue",        35),
        ("SUNRISE_START","hue",        25),
        ("LATE_NIGHT",   "brightness", 40),
    ]

    @pytest.mark.parametrize("profile,field,value", HSB_CASES)
    def test_set_reflects_in_load_profiles_and_resets(self, profile, field, value, tmp_config):
        from controller.config import PROFILE_DEFAULTS, load_profiles

        _pset(profile, field, value)

        # reflects in load_profiles()
        p = load_profiles()[profile]
        assert getattr(p, field) == value, \
            f"load_profiles()[{profile!r}].{field} after set: expected {value}, got {getattr(p, field)}"

        # written to config.json
        raw = _load_raw(tmp_config)
        assert raw.get("profiles", {}).get(profile, {}).get(field) == value, \
            f"config.json profiles.{profile}.{field} should be {value}"

        # reset removes it and restores default
        _preset(profile)
        assert profile not in _load_raw(tmp_config).get("profiles", {}), \
            f"{profile} should be absent from config.json after reset"
        default = getattr(PROFILE_DEFAULTS[profile], field)
        assert getattr(load_profiles()[profile], field) == default, \
            f"load_profiles()[{profile!r}].{field} after reset: expected default {default}"


class TestProfileCTFields:
    """MORNING profile: color_temp and brightness (CT mode)."""

    def test_morning_ct_roundtrip(self, tmp_config):
        from controller.config import PROFILE_DEFAULTS, load_profiles

        _pset("MORNING", "color_temp", 5500)
        _pset("MORNING", "brightness", 90)
        p = load_profiles()["MORNING"]
        assert p.color_temp == 5500, f"MORNING color_temp should be 5500, got {p.color_temp}"
        assert p.brightness == 90,   f"MORNING brightness should be 90, got {p.brightness}"

        raw = _load_raw(tmp_config)
        assert raw["profiles"]["MORNING"]["color_temp"] == 5500
        assert raw["profiles"]["MORNING"]["brightness"] == 90

        _preset("MORNING")
        p_reset = load_profiles()["MORNING"]
        assert p_reset.color_temp == PROFILE_DEFAULTS["MORNING"].color_temp, \
            "MORNING color_temp should be default after reset"
        assert p_reset.brightness == PROFILE_DEFAULTS["MORNING"].brightness, \
            "MORNING brightness should be default after reset"


class TestProfileIsolationAndCoexistence:
    """Setting one field must not affect others; multiple profiles coexist."""

    def test_set_one_field_does_not_change_others(self):
        from controller.config import PROFILE_DEFAULTS, load_profiles
        default = PROFILE_DEFAULTS["NIGHT"]
        _pset("NIGHT", "hue", 20)
        p = load_profiles()["NIGHT"]
        assert p.saturation == default.saturation, \
            f"Setting hue should not change saturation (expected {default.saturation}, got {p.saturation})"
        assert p.brightness == default.brightness, \
            f"Setting hue should not change brightness (expected {default.brightness}, got {p.brightness})"
        assert p.mode == default.mode, \
            f"Setting hue should not change mode (expected {default.mode!r}, got {p.mode!r})"

    def test_multiple_profiles_coexist(self, tmp_config):
        from controller.config import load_profiles
        _pset("NIGHT",   "hue",        20)
        _pset("PARTY",   "hue",        120)
        _pset("MORNING", "brightness", 90)

        p = load_profiles()
        assert p["NIGHT"].hue        == 20,  f"NIGHT hue should be 20"
        assert p["PARTY"].hue        == 120, f"PARTY hue should be 120"
        assert p["MORNING"].brightness == 90, f"MORNING brightness should be 90"

        raw = _load_raw(tmp_config)
        assert raw["profiles"]["NIGHT"]["hue"]           == 20
        assert raw["profiles"]["PARTY"]["hue"]           == 120
        assert raw["profiles"]["MORNING"]["brightness"]  == 90

    def test_reset_one_profile_leaves_others_intact(self, tmp_config):
        _pset("NIGHT", "hue", 20)
        _pset("PARTY", "hue", 120)
        _preset("NIGHT")
        raw = _load_raw(tmp_config)
        assert "NIGHT" not in raw.get("profiles", {}), \
            "NIGHT should be removed from config.json after reset"
        assert raw.get("profiles", {}).get("PARTY", {}).get("hue") == 120, \
            "PARTY override should survive resetting NIGHT"

    def test_two_fields_same_profile_merge_correctly(self, tmp_config):
        from controller.config import load_profiles
        _pset("NIGHT", "hue", 20)
        _pset("NIGHT", "saturation", 90)
        p = load_profiles()["NIGHT"]
        assert p.hue        == 20, f"NIGHT hue should be 20, got {p.hue}"
        assert p.saturation == 90, f"NIGHT saturation should be 90, got {p.saturation}"
        raw = _load_raw(tmp_config)
        assert raw["profiles"]["NIGHT"] == {"hue": 20, "saturation": 90}, \
            f"Only set fields should be in JSON, got {raw['profiles']['NIGHT']}"


class TestProfileInvalidValues:
    """Invalid profile inputs exit non-zero without writing."""

    @pytest.mark.parametrize("profile,field,bad_value,reason", [
        ("NIGHT",   "hue",        "361",  "hue > 360"),
        ("NIGHT",   "hue",        "-1",   "hue < 0"),
        ("NIGHT",   "saturation", "101",  "sat > 100"),
        ("NIGHT",   "saturation", "-1",   "sat < 0"),
        ("NIGHT",   "brightness", "101",  "brightness > 100"),
        ("MORNING", "color_temp", "1199", "CT below min"),
        ("MORNING", "color_temp", "6501", "CT above max"),
        ("NIGHT",   "mode",       "xyz",  "invalid mode"),
        ("NIGHT",   "hue",        "abc",  "not a number"),
    ])
    def test_invalid_value_does_not_write(self, profile, field, bad_value, reason, tmp_config):
        from nanoleaf_cli.commands.profile import run_set
        with pytest.raises(SystemExit):
            run_set(_args(name=profile, field=field, value=bad_value, verbose=False))
        raw = _load_raw(tmp_config)
        assert raw.get("profiles", {}).get(profile, {}).get(field) is None, \
            f"Invalid {profile}.{field}={bad_value!r} ({reason}) must not be written"

    def test_unknown_profile_name_rejected(self, tmp_config):
        from nanoleaf_cli.commands.profile import run_set
        with pytest.raises(SystemExit):
            run_set(_args(name="NONEXISTENT", field="hue", value="20", verbose=False))
        assert _load_raw(tmp_config).get("profiles", {}).get("NONEXISTENT") is None


class TestProfileListGet:
    """profile list and profile get output."""

    PROFILE_NAMES = ["SUNRISE_START", "SUNRISE_END", "MORNING", "DAYTIME_ON",
                     "NIGHT", "LATE_NIGHT", "PARTY", "OFF"]

    def test_list_shows_all_profiles_with_overrides(self, capsys):
        from nanoleaf_cli.commands.profile import run_list, run_set
        run_set(_args(name="NIGHT", field="hue", value="20", verbose=False))
        capsys.readouterr()
        run_list(_args())
        out = capsys.readouterr().out
        for name in self.PROFILE_NAMES:
            assert name in out, f"profile list missing {name!r}"
        assert "20" in out, "profile list should show overridden NIGHT hue=20"

    def test_get_shows_default_and_override_for_every_profile(self, capsys):
        from controller.config import PROFILE_DEFAULTS
        from nanoleaf_cli.commands.profile import run_get
        for name in self.PROFILE_NAMES:
            capsys.readouterr()
            run_get(_args(name=name))
            out = capsys.readouterr().out
            assert name in out, \
                f"profile get {name} output should contain the profile name; got:\n{out}"
        # Also verify override shows after set
        from nanoleaf_cli.commands.profile import run_set
        run_set(_args(name="NIGHT", field="hue", value="20", verbose=False))
        capsys.readouterr()
        run_get(_args(name="NIGHT"))
        out = capsys.readouterr().out
        assert "20" in out, f"profile get NIGHT after override should show hue=20; got:\n{out}"


# ---------------------------------------------------------------------------
# COLOR-NAME COMMAND
# ---------------------------------------------------------------------------

class TestColorName:

    def test_hex_rgb_cmyk_all_write_range_and_overwrite(self, tmp_config):
        from controller.config import _config_cache
        from nanoleaf_cli.commands.config import run_color_name

        # HEX pure red → hue≈0
        run_color_name(_args(hex="FF0000", rgb=None, cmyk=None, name="scarlet", verbose=False))
        raw = _load_raw(tmp_config)
        assert any("scarlet" in v for v in raw.get("color_names", {}).values()), \
            f"color_names should contain 'scarlet' after --hex FF0000; got {raw.get('color_names')}"

        # RGB same color → same range key
        hex_ranges = set(raw["color_names"].keys())
        tmp_config.write_text("{}")
        _config_cache.clear()
        run_color_name(_args(hex=None, rgb="255,0,0", cmyk=None, name="scarlet-rgb", verbose=False))
        raw_rgb = _load_raw(tmp_config)
        rgb_ranges = set(raw_rgb["color_names"].keys())
        assert hex_ranges == rgb_ranges, \
            f"--hex FF0000 and --rgb 255,0,0 should produce the same range; hex={hex_ranges}, rgb={rgb_ranges}"

        # CMYK (0,100,100,0) → RGB(255,0,0) → same range
        tmp_config.write_text("{}")
        _config_cache.clear()
        run_color_name(_args(hex=None, rgb=None, cmyk="0,100,100,0", name="scarlet-cmyk", verbose=False))
        raw_cmyk = _load_raw(tmp_config)
        cmyk_ranges = set(raw_cmyk["color_names"].keys())
        assert hex_ranges == cmyk_ranges, \
            f"--cmyk 0,100,100,0 should produce the same range as --hex FF0000; got {cmyk_ranges}"

        # Overwrite same range replaces the name
        run_color_name(_args(hex=None, rgb="255,0,0", cmyk=None, name="updated", verbose=False))
        raw_upd = _load_raw(tmp_config)
        assert "updated" in raw_upd["color_names"].values(), \
            "Second assignment should overwrite previous name"
        assert "scarlet-cmyk" not in raw_upd["color_names"].values(), \
            "Previous name should be replaced by updated name"

    def test_color_name_wiped_by_reset_all(self, tmp_config):
        from nanoleaf_cli.commands.config import run_color_name, run_reset
        run_color_name(_args(hex="FF0000", rgb=None, cmyk=None, name="scarlet", verbose=False))
        run_reset(_args(key=None, all=True))
        assert _load_raw(tmp_config) == {}, \
            "config reset --all should wipe color_names"
