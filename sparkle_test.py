#!/usr/bin/env python3
"""sparkle_test.py — does sparkle actually stop the brightness flicker?

Holds WHITE at a known-flickering brightness (ceiling) while dimming an
INCREASING number of panels to a low floor. Watch the still-bright (ceiling)
panels: if they stop flickering as more panels are dimmed, sparkle works; if
they keep flickering no matter how many are dimmed, only a hard cap will help.

Holds the preview lock (cron skips) and restores lamp state on exit (incl. Ctrl+C).

Run on the Pi from the repo root:   python3 sparkle_test.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dotenv import load_dotenv
load_dotenv(os.path.join(HERE, ".env"))

import filelock
from nanoleaf.nanoleafLight import NanoleafLight
from nanoleaf.sparkle import hsb_to_rgb
from controller.state import get_preview_lock

# --- tune these -------------------------------------------------------------
HOLD = 30                       # seconds per step
HUE, SAT = 0, 0                 # 0,0 = white (worst case); try 240,100 for blue
CEILING_BRI = 70                # a brightness that FLICKERS with all panels lit
FLOOR_BRI = 15                  # brightness of the dimmed panels
DIM_COUNTS = [0, 10, 20, 30, 40]  # how many panels to dim, stepped up
TRANS = 4
# ---------------------------------------------------------------------------


def build_static(panel_ids, dim_ids, floor_rgb, ceiling_rgb, trans=TRANS):
    toks = [str(len(panel_ids))]
    dim = set(dim_ids)
    for pid in panel_ids:
        r, g, b = floor_rgb if pid in dim else ceiling_rgb
        toks += [str(pid), "1", str(r), str(g), str(b), "0", str(trans)]
    return {"command": "display", "version": "2.0", "animType": "static",
            "animData": " ".join(toks), "loop": False, "palette": []}


def even(ids, k):
    if k <= 0:
        return []
    step = max(1, len(ids) // k)
    return ids[::step][:k]


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
    try:
        panel_ids = sorted(light.get_panel_ids())
    except Exception as exc:
        print(f"Could not read panel IDs: {exc}")
        return

    ceiling_rgb = hsb_to_rgb(HUE, SAT, CEILING_BRI)
    floor_rgb = hsb_to_rgb(HUE, SAT, FLOOR_BRI)

    lock = get_preview_lock()
    try:
        lock.acquire()
    except filelock.Timeout:
        print("A preview/experiment is already running. Aborting.")
        return

    print(f"Sparkle test: {len(panel_ids)} panels, ceiling bri {CEILING_BRI} "
          f"(HSB {HUE},{SAT}), floor bri {FLOOR_BRI}. {HOLD}s per step.")
    print("Watch the BRIGHT panels — do they stop flickering as more are dimmed?\n")

    try:
        light.power_on()
        for k in DIM_COUNTS:
            dim_ids = even(panel_ids, k)
            print(f"  dimming {k:2d}/{len(panel_ids)} panels  "
                  f"({len(panel_ids) - k} still bright)  — holding {HOLD}s ...", flush=True)
            light.write_effect(build_static(panel_ids, dim_ids, floor_rgb, ceiling_rgb))
            time.sleep(HOLD)
    except KeyboardInterrupt:
        print("\nInterrupted — restoring...")
    finally:
        try:
            light.power_off()
            time.sleep(0.3)
            if saved.get("colorMode") == "ct":
                light.set_color_temp_and_brightness(saved.get("ct", 4000), saved.get("brightness", 50))
            else:
                light.set_hsb(saved.get("hue", 30), saved.get("sat", 50), saved.get("brightness", 50))
            if not saved.get("on", True):
                light.power_off()
        finally:
            lock.release()
        print("\nRestored. Report: at how many dimmed panels (if any) did the bright "
              "panels STOP flickering? 'never' means sparkle doesn't fix it -> hard cap needed.")


if __name__ == "__main__":
    main()
