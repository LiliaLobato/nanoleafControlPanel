"""error command — last state error + recent log ERROR entries."""

import sys

from controller.log_setup import LOG_PATH
from controller.state import load_state


def run(args, now=None):
    state = load_state()
    err = state.get("last_error")

    if err:
        ts = err.get("timestamp", "?")
        etype = err.get("type", "?")
        msg = err.get("message", "?")
        print(f"  state error  [{ts}] {etype}: {msg}")
    else:
        print("  no errors recorded in state")

    n = getattr(args, "n", 1)
    if not LOG_PATH.exists():
        print(f"  (log file not found: {LOG_PATH})", file=sys.stderr)
        return

    error_lines = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            if " ERROR " in line:
                error_lines.append(line.rstrip())

    if not error_lines:
        print("  (no ERROR entries in log)")
        return

    print(f"\n  last {n} ERROR log {'entry' if n == 1 else 'entries'}:")
    for line in error_lines[-n:]:
        print(f"    {line}")
