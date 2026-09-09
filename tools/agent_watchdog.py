#!/usr/bin/env python3
"""
Agent watchdog — keeps the Douyin agent alive on every connected phone.

For a matrix of phones wired to one PC/Mac over USB, this polls each device and:
  • re-launches the agent app if its process died / was killed by the OS;
  • (USB only) re-asserts `adb reverse` so the phone can reach a backend on
    127.0.0.1 — skip with --lan if phones reach the server by LAN IP instead.

Cross-platform: needs only `adb` on PATH. Runs on Windows and macOS alike.

Usage:
    python agent_watchdog.py                 # USB, default 8010, every 30s
    python agent_watchdog.py --lan           # phones use LAN IP (no adb reverse)
    python agent_watchdog.py --port 8010 --interval 30
    python agent_watchdog.py --serials 6127f7f6,abc123   # only these devices
"""
import argparse
import subprocess
import sys
import time

PKG = "com.example.douyinagent"
MAIN_ACTIVITY = f"{PKG}/.MainActivity"
SERVICE = f"{PKG}/.service.AgentForegroundService"


def adb(args, serial=None):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def connected_devices():
    _, out = adb(["devices"])
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line:
            devices.append(line.split("\t", 1)[0])
    return devices


def app_running(serial):
    # pidof returns a pid (and exit 0) when the process is alive.
    code, out = adb(["shell", "pidof", PKG], serial)
    return code == 0 and out.strip() != ""


def relaunch(serial):
    # Launch the activity; it (re)starts the foreground service if autoStart is on.
    adb(["shell", "am", "start", "-n", MAIN_ACTIVITY], serial)


def ensure_reverse(serial, port):
    # Idempotent; safe to call every tick.
    adb(["reverse", f"tcp:{port}", f"tcp:{port}"], serial)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--interval", type=int, default=30, help="seconds between checks")
    ap.add_argument("--lan", action="store_true", help="phones use LAN IP; skip adb reverse")
    ap.add_argument("--serials", default="", help="comma-separated allowlist of device serials")
    args = ap.parse_args()
    only = {s.strip() for s in args.serials.split(",") if s.strip()}

    print(f"[watchdog] start · port={args.port} · interval={args.interval}s · "
          f"mode={'LAN' if args.lan else 'USB(reverse)'}", flush=True)
    while True:
        try:
            devices = connected_devices()
            if only:
                devices = [d for d in devices if d in only]
            if not devices:
                print(f"[{ts()}] no devices connected", flush=True)
            for serial in devices:
                if not args.lan:
                    ensure_reverse(serial, args.port)
                if app_running(serial):
                    print(f"[{ts()}] {serial} OK", flush=True)
                else:
                    print(f"[{ts()}] {serial} DOWN -> relaunch", flush=True)
                    relaunch(serial)
        except KeyboardInterrupt:
            print("\n[watchdog] stopped", flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[{ts()}] error: {exc}", flush=True)
        time.sleep(args.interval)


def ts():
    return time.strftime("%H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
