"""debug commands — on, off."""

from controller.config import save_config
from nanoleaf_cli._config_io import load_raw_config


def _set_verbose(value: bool, label: str) -> None:
    raw = load_raw_config()
    raw["verbose"] = value
    save_config(raw)
    print(f"  ✓ {label}")


def run_on(args, now=None):
    _set_verbose(True, "verbose logging enabled")


def run_off(args, now=None):
    _set_verbose(False, "verbose logging disabled")
