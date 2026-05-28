"""logs command — tail or follow the controller log file."""

import sys
import time

from controller.log_setup import LOG_PATH


def run(args, now=None):
    n = getattr(args, "n", None)

    if not LOG_PATH.exists():
        print(f"log file not found: {LOG_PATH}", file=sys.stderr)
        sys.exit(1)

    if n is not None:
        with open(LOG_PATH) as f:
            lines = f.readlines()
        for line in lines[-n:]:
            print(line, end="")
        return

    # Follow mode: seek to end then poll for new lines
    with open(LOG_PATH) as f:
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
