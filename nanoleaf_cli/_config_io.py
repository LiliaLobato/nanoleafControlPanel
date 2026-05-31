"""Shared config file I/O for CLI commands.

load_raw_config() is the single authoritative reader for the raw
config.json overlay. All CLI commands that need to read before writing
use this instead of re-implementing their own file read.
"""

import json
import controller.config as _cfg_module


def load_raw_config() -> dict:
    """Read config.json as a raw dict, returning {} on missing file or parse error."""
    path = _cfg_module.CONFIG_PATH
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
