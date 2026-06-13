"""inspect_lamp_effect.py

One-shot script to fetch a built-in effect from the lamp and pretty-print
its raw JSON, including the full animData string.

Usage (from repo root):
    python scripts/inspect_lamp_effect.py

Reads NANOLEAF_IP and NANOLEAF_AUTH_TOKEN from environment / .env.
Prints the first available effect's full JSON so you can verify:
  - animData string layout
  - W (white channel) field presence
  - transTime unit
  - panel ID field name inside positionData
"""

import json
import os
import sys


def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    _load_env()
    ip = os.environ.get("NANOLEAF_IP_ADDRESS") or os.environ.get("NANOLEAF_IP")
    token = os.environ.get("NANOLEAF_AUTH_TOKEN")
    if not ip or not token:
        print("ERROR: set NANOLEAF_IP_ADDRESS and NANOLEAF_AUTH_TOKEN in .env or environment")
        sys.exit(1)

    import requests

    base = f"http://{ip}:16021/api/v1/{token}"

    # 1. Fetch device info to get effect list and panel layout
    print("=== GET / (device info) ===")
    r = requests.get(base, timeout=(3, 5))
    r.raise_for_status()
    info = r.json()

    effects_list = info.get("effects", {}).get("effectsList", [])
    print(f"Available effects: {effects_list}\n")

    print("=== panelLayout.layout (first 3 panels) ===")
    position_data = info["panelLayout"]["layout"]["positionData"]
    for p in position_data[:3]:
        print(f"  {p}")
    print(f"  ... ({len(position_data)} panels total)\n")

    # 2. Fetch raw animData for the first effect
    if not effects_list:
        print("No effects available to inspect.")
        return

    effect_name = effects_list[0]
    print(f"=== GET /effects (requesting '{effect_name}') ===")
    payload = json.dumps({"write": {"command": "request", "animName": effect_name}})
    r2 = requests.put(f"{base}/effects", data=payload,
                      headers={"Content-Type": "application/json"}, timeout=(3, 10))
    r2.raise_for_status()
    effect = r2.json()

    print(json.dumps(effect, indent=2))

    anim = effect.get("animData", "")
    if anim:
        print("\n=== animData tokens (first 30) ===")
        tokens = anim.split()
        print(tokens[:30])
        print(f"\nTotal token count: {len(tokens)}")
        num_panels = int(tokens[0])
        print(f"Declared panel count: {num_panels}")


if __name__ == "__main__":
    main()
