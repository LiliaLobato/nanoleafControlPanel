#!/usr/bin/env python3
"""flicker_sweep.py — automated flicker-calibration sweep.

Holds each test colour at stepped brightness for HOLD seconds so you can watch
the panels and note the brightness at which each colour STARTS to flicker.

While running it HOLDS the preview lock (the cron controller skips its ticks so
it won't fight the experiment) and disables the current-guard. The lamp state
and guard config are RESTORED on exit — including Ctrl+C.

Run on the Pi from the repo root:   python3 flicker_sweep.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import filelock
from nanoleaf.nanoleafLight import NanoleafLight
from controller.state import get_preview_lock
from controller.config import load_config

# --- tune these -------------------------------------------------------------
HOLD = 30                                   # seconds per (colour, brightness)
BRIGHTNESS_STEPS = [100, 80, 60, 40, 20]    # stepped down; note where flicker STOPS
COLORS = [                                  # (name, hue, sat); sat 0 = white
    ("RED",   0,   100),
    ("GREEN", 120, 100),
    ("BLUE",  240, 100),
    ("WHITE", 0,   0),
    ("AMBER", 30,  100),                    # warm control — expect no flicker
    ("CYAN",  180, 100),                    # G+B — worst case
]
# ---------------------------------------------------------------------------


def set_guard(enabled):
    subprocess.run(
        [sys.executable, os.path.join(HERE, "nanoleaf_cli.py"),
         "config", "set", "current_guard_enabled", "true" if enabled else "false"],
        check=False,
    )


def main():
    light = NanoleafLight(
        os.getenv("NANOLEAF_NAME", "Nanoleaf"),
        os.getenv("NANOLEAF_IP_ADDRESS", ""),
        os.getenv("NANOLEAF_AUTH_TOKEN", ""),
    )

    saved = light.get_full_state()
    if not saved:
        print("Lamp unreachable — aborting.")
        return
    guard_was = load_config().current_guard_enabled

    lock = get_preview_lock()
    try:
        lock.acquire()
    except filelock.Timeout:
        print("A preview/experiment is already running (preview lock held). Aborting.")
        return

    n = len(COLORS) * len(BRIGHTNESS_STEPS)
    print(f"Flicker sweep: {len(COLORS)} colours x {len(BRIGHTNESS_STEPS)} levels x {HOLD}s "
          f"= ~{n * HOLD // 60} min. Ctrl+C stops early; state is restored either way.\n")

    try:
        set_guard(False)
        light.power_on()
        for name, hue, sat in COLORS:
            print(f"\n=== {name}  (hue={hue}, sat={sat}) ===")
            for bri in BRIGHTNESS_STEPS:
                print(f"  {name} @ brightness {bri:3d}  — holding {HOLD}s ...", flush=True)
                light.set_hsb(hue, sat, bri)
                time.sleep(HOLD)
    except KeyboardInterrupt:
        print("\nInterrupted — restoring...")
    finally:
        set_guard(guard_was)
        try:
            if saved.get("colorMode") == "ct":
                light.set_color_temp_and_brightness(saved.get("ct", 4000), saved.get("brightness", 50))
            else:
                light.set_hsb(saved.get("hue", 30), saved.get("sat", 50), saved.get("brightness", 50))
            if not saved.get("on", True):
                light.power_off()
        finally:
            lock.release()
        print("\nRestored lamp + guard config. Next cron tick resumes the schedule.")
        print("Report onset brightness per colour, e.g.: "
              "RED never, GREEN 60, BLUE 40, WHITE 60, AMBER never, CYAN 40")


if __name__ == "__main__":
    main()
