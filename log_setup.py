"""log_setup.py

Logging configuration for the Nanoleaf controller.
Sets up a RotatingFileHandler (5 MB × 5 backups = 25 MB cap) so the cron
job never needs a log redirect. Verbose mode switches the root level to DEBUG.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import Config

LOG_DIR  = Path.home() / ".local" / "state" / "nanoleafControlPanel"
LOG_PATH = LOG_DIR / "nanoleaf.log"


def setup_logging(config: Config) -> None:
    """Configure the root logger for the controller.

    Attaches a RotatingFileHandler and sets level to DEBUG when verbose=True,
    INFO otherwise. Safe to call multiple times — duplicate handlers are avoided.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # already configured

    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))

    root.addHandler(handler)
    root.setLevel(logging.DEBUG if config.verbose else logging.INFO)
