"""logs command — tail or follow the controller log file."""

import sys
import time
from collections import deque

from controller.log_setup import LOG_PATH


def run(args, now=None):
    n = getattr(args, "n", None)

    if not LOG_PATH.exists():
        print(f"log file not found: {LOG_PATH}", file=sys.stderr)
        sys.exit(1)

    if n is not None:
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in deque(f, maxlen=n):
                print(line, end="")
        return

    # Follow mode: seek to end then poll for new lines
    with open(LOG_PATH, encoding="utf-8") as f:
        f.seek(0, 2)
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.2)
        except KeyboardInterrupt:
            pass
