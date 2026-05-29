#!/usr/bin/env python3
"""Toggle the Nanoleaf lamp on/off. Run directly or via toggle_lamp.bat."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from nanoleaf.nanoleafLight import NanoleafLight

ip = os.environ.get("NANOLEAF_IP_ADDRESS", "")
token = os.environ.get("NANOLEAF_AUTH_TOKEN", "")

if not ip or not token:
    print("Error: NANOLEAF_IP_ADDRESS and NANOLEAF_AUTH_TOKEN not found in .env")
    sys.exit(1)

light = NanoleafLight(name="lamp", ip=ip, auth_token=token)
is_on = light.get_power()

if is_on:
    light.power_off()
    print("Lamp off.")
else:
    light.power_on()
    print("Lamp on.")
