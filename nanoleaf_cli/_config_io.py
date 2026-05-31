"""Shared config file I/O for CLI commands.

Single authoritative _load_raw implementation used by all commands that
need to read the raw config.json overlay before calling save_config().
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
