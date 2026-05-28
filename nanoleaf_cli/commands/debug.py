"""debug commands — on, off."""

import json

from controller.config import CONFIG_PATH, save_config


def _load_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def run_on(args, now=None):
    raw = _load_raw()
    raw["verbose"] = True
    save_config(raw)
    print("  ✓ verbose logging enabled")


def run_off(args, now=None):
    raw = _load_raw()
    raw["verbose"] = False
    save_config(raw)
    print("  ✓ verbose logging disabled")
