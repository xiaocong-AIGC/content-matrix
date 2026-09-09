#!/usr/bin/env python3
"""
USB auto-onboarding — watch for newly-wired phones and auto-provision them.

Polls `adb devices`; when a device appears that does NOT have our agent app
installed, it runs the full zero-touch provisioning (install APK → enable
accessibility → battery whitelist → push config → auto-start), so plugging a new
phone into the USB hub makes it join the matrix with no taps.

Device names are assigned `<prefix>-NN` and remembered per-serial in a small JSON
so a given phone keeps its name across reconnects/restarts.

Usage:
    python auto_provision_watch.py --server http://192.168.1.8:8010/api/v1
    python auto_provision_watch.py --server http://… --name-prefix 上海 --interval 5
    python auto_provision_watch.py --server http://… --reprovision   # re-push to all, even if installed

Stop with Ctrl-C.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from provision_device import (
    DEFAULT_APK,
    PKG,
    adb,
    devices,
    persisted_provision_key,
    provision,
)

STATE_FILE = Path(__file__).with_name(".auto_provision_state.json")


def is_installed(serial: str) -> bool:
    out = adb(["shell", "pm", "list", "packages", PKG], serial)
    return PKG in out


def is_running(serial: str) -> bool:
    return adb(["shell", "pidof", PKG], serial).strip() != ""


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"names": {}, "counter": 0}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def name_for(serial: str, state: dict, prefix: str) -> str:
    names = state["names"]
    if serial not in names:
        state["counter"] += 1
        names[serial] = f"{prefix}-{state['counter']:02d}"
        save_state(state)
    return names[serial]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="backend base URL")
    ap.add_argument("--apk", default=DEFAULT_APK)
    ap.add_argument("--name-prefix", default="设备")
    ap.add_argument("--provision-key", default=None)
    ap.add_argument("--interval", type=float, default=5.0, help="poll seconds")
    ap.add_argument(
        "--reprovision",
        action="store_true",
        help="re-provision even devices that already have the app installed",
    )
    args = ap.parse_args()
    if args.provision_key is None:
        args.provision_key = persisted_provision_key()

    state = load_state()
    handled: set[str] = set()  # serials onboarded this run (avoid re-loops)
    print(
        f"[auto] watching for new devices → {args.server} "
        f"(prefix '{args.name_prefix}', every {args.interval}s). Ctrl-C to stop.",
        flush=True,
    )
    try:
        while True:
            for serial in devices():
                if serial in handled:
                    continue
                installed = is_installed(serial)
                if installed and not args.reprovision:
                    # Already has the app — adopt its name, ensure it's not a
                    # fresh phone needing onboarding; leave it to the watchdog.
                    handled.add(serial)
                    print(f"[auto] {serial}: app present, skipping.", flush=True)
                    continue
                name = name_for(serial, state, args.name_prefix)
                print(f"[auto] NEW device {serial} → provisioning as {name}", flush=True)
                try:
                    ok = provision(serial, args.server, name, args.apk, args.provision_key)
                    if ok:
                        handled.add(serial)
                        print(f"[auto] {serial} ({name}) onboarded ✓", flush=True)
                    else:
                        print(f"[auto] {serial} provision incomplete, will retry.", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[auto] {serial} FAILED: {exc}", flush=True)
            # Drop serials that unplugged, so re-inserting re-checks them.
            handled.intersection_update(set(devices()))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[auto] stopped.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
