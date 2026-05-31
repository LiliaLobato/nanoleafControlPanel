"""debug commands — on, off."""

from controller.config import save_config
from nanoleaf_cli._config_io import load_raw_config


def run_on(args, now=None):
    raw = load_raw_config()
    raw["verbose"] = True
    save_config(raw)
    print("  ✓ verbose logging enabled")


def run_off(args, now=None):
    raw = load_raw_config()
    raw["verbose"] = False
    save_config(raw)
    print("  ✓ verbose logging disabled")
