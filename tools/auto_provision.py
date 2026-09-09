#!/usr/bin/env python3
"""
Auto-provision watcher — the "plug it in and it joins the matrix" daemon.

Polls `adb devices`; the moment a phone WITHOUT our agent shows up, it runs the
full zero-touch provision flow (install → accessibility → battery whitelist →
overlay → adb reverse tunnel → push config + auto-start). Devices that already
have the agent are left alone (just keep their USB→backend tunnel alive).

This is the missing half of the matrix: `provision_device.py` is a one-shot you
run by hand; this watches continuously so new devices need ZERO PC interaction.

Usage:
    python auto_provision.py                      # 127.0.0.1 via adb reverse (USB)
    python auto_provision.py --server http://192.168.1.8:8010/api/v1 --no-reverse
    python auto_provision.py --reprovision        # force re-provision every device
"""
import argparse
import os
import subprocess
import sys
import time

# Windows consoles default to GBK, which chokes on Chinese device names / symbols
# in our log lines — force UTF-8 so logging never throws mid-provision.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import provision_device as pd  # same tools/ dir — reuse the proven flow


def agent_installed(serial: str) -> bool:
    out = pd.adb(["shell", "pm", "list", "packages", pd.PKG], serial)
    return pd.PKG in out


def ensure_reverse(serial: str, port: int) -> None:
    """USB tunnel so the phone reaches a backend bound on 127.0.0.1. Harmless to
    re-assert; it must be re-applied after every reconnect."""
    pd.adb(["reverse", f"tcp:{port}", f"tcp:{port}"], serial)


def device_label(serial: str) -> str:
    # 云机的 serial 是 `192.168.1.200:6021`，`serial[:6]` 会得到 "10.246" ——
    # 17 台新机会全叫「PDEM10-10.246」，既看不出是哪台也不唯一。按端口命名，
    # 和现有 20 台的「云机6001」保持一致。
    if pd.is_network(serial):
        return f"云机{serial.rsplit(':', 1)[-1]}"
    model = pd.adb(["shell", "getprop", "ro.product.model"], serial).strip()
    model = model.replace(" ", "")
    return f"{model}-{serial[:6]}" if model else serial


def connected() -> list[str]:
    # Only fully-ready devices; skip "unauthorized" / "offline".
    return pd.devices()


def safe(fn, default, what):
    """守护进程**绝对不能死**：它是唯一在续 `adb reverse` 隧道的东西，它一挂,
    几十台手机会在下一次隧道掉线时集体失联(2026-09-03 全机房静音 3.5 小时就是
    这么来的)。所以每一步 adb 调用都兜住,大不了本轮跳过、下一轮再来。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[auto] {what} 失败，本轮跳过：{exc}", flush=True)
        return default


def _expand(spec: str) -> list[str]:
    """"192.168.1.200:6001-6037, 1.2.3.4:5555" → 逐个 host:port。"""
    eps = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        host, _, port = part.rpartition(":")
        if host and "-" in port:
            lo, hi = port.split("-", 1)
            if lo.isdigit() and hi.isdigit():
                eps += [f"{host}:{p}" for p in range(int(lo), int(hi) + 1)]
        elif host:
            eps.append(part)
    return eps


def connect_cloud_phones(server: str) -> int:
    """新加的云机端口必须先 `adb connect` 才会出现在 `adb devices` 里。
    以前只有编排器做这件事，所以「检测设备」永远看不到刚扩容的机器——
    扫描前自己连一遍，这个按钮才名副其实。地址来自后端设置（首次配置向导里填的
    那一栏），env MATRIX_ADB_ENDPOINTS 兜底。"""
    spec = ""
    try:
        import json
        import urllib.request
        base = server.rstrip("/")
        with urllib.request.urlopen(f"{base}/agent/adb-endpoints", timeout=5) as r:
            spec = json.load(r).get("endpoints", "") or ""
    except Exception:  # noqa: BLE001 — 后端没起来就退回 env
        pass
    spec = spec or os.environ.get("MATRIX_ADB_ENDPOINTS", "").strip()
    if not spec:
        return 0
    already = set(connected())
    added = 0
    for ep in _expand(spec):
        if ep in already:
            continue
        try:
            out = subprocess.run(["adb", "connect", ep], capture_output=True,
                                 text=True, errors="replace",
                                 timeout=20).stdout or ""
        except Exception:  # noqa: BLE001 — 一个端口连不上不该拖垮整轮扫描
            continue
        if "connected to" in out:
            added += 1
    if added:
        print(f"[auto] adb connect 新增 {added} 台云机", flush=True)
    return added


def config_state(serial: str) -> str:
    """装了 Agent 的机器还得看它有没有真的进控制台（云机镜像克隆出来的最常见：
    APK 在、配置不在，或带着上一台机器的失效凭证）。
    new=从没跑起来过 / stale=跑过但没注册上 / ok=已注册 / unknown=读不到（不动它）"""
    xml = pd.adb(
        ["shell", "run-as", pd.PKG, "cat",
         f"/data/data/{pd.PKG}/shared_prefs/douyin_agent.xml"],
        serial,
    )
    if "No such file" in xml:
        return "new"
    if "<map" not in xml:
        return "unknown"
    return "ok" if 'name="agent_token"' in xml else "stale"




def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--server",
        default="http://127.0.0.1:8010/api/v1",
        help="backend base URL the agent registers against",
    )
    ap.add_argument("--apk", default=pd.DEFAULT_APK)
    ap.add_argument("--interval", type=float, default=60.0, help="poll seconds")
    ap.add_argument(
        "--once",
        action="store_true",
        help="scan once and exit (used by the 检测设备 button via the backend)",
    )
    ap.add_argument("--reverse-port", type=int, default=8010)
    ap.add_argument(
        "--no-reverse",
        action="store_true",
        help="skip adb reverse (use when --server is a real LAN IP)",
    )
    ap.add_argument(
        "--reprovision",
        action="store_true",
        help="re-run provisioning even for devices that already have the agent",
    )
    args = ap.parse_args()
    key = pd.persisted_provision_key(verbose=True)

    if args.once:
        print(f"[auto] one-shot scan · server={args.server}", flush=True)
    else:
        print(
            f"[auto] watching for new devices · server={args.server} "
            f"· reverse={'off' if args.no_reverse else args.reverse_port} "
            f"· interval={args.interval}s. Ctrl-C to stop.",
            flush=True,
        )
    provisioned: set[str] = set()
    newly = 0
    warned_missing_apk = False
    try:
        while True:
            safe(lambda: connect_cloud_phones(args.server), 0, "adb connect")
            for serial in safe(connected, [], "adb devices"):
                try:
                    if not args.no_reverse:
                        ensure_reverse(serial, args.reverse_port)

                    if serial in provisioned and not args.reprovision:
                        continue

                    if agent_installed(serial) and not args.reprovision:
                        # Pre-existing agent (e.g. a known phone reconnecting) —
                        # don't reinstall; the watchdog handles relaunch. But an
                        # agent that never registered (cloned cloud-phone image,
                        # or a provisioning run that had no key) must still get
                        # its config pushed, or it sits at「设备凭证失效」forever.
                        if serial in provisioned:
                            continue
                        state = config_state(serial)
                        # new/stale 的机器都还没进控制台（拿不到任务，也就不可能
                        # 正在发布），不用管前台是什么，直接补配置。
                        if state in ("new", "stale"):
                            name = device_label(serial)
                            print(f"[auto] {serial} ({name}): 已装 Agent 但未注册 "
                                  f"→ 补做配置", flush=True)
                            # 走完整流程（provision 内部会跳过安装）：只补 base_url
                            # 不补无障碍的话，机器会「在线但未就绪」，照样发不了。
                            if pd.provision(serial, args.server, name, args.apk, key):
                                newly += 1
                        else:
                            print(f"[auto] {serial}: agent already present, "
                                  f"tunnel ensured — skipping install", flush=True)
                        provisioned.add(serial)
                        continue

                    import os
                    if not os.path.exists(args.apk):
                        if not warned_missing_apk:
                            print(f"[auto] APK not found at {args.apk} — build it "
                                  f"first (assembleDebug).", file=sys.stderr, flush=True)
                            warned_missing_apk = True
                        continue

                    name = device_label(serial)
                    print(f"[auto] NEW device {serial} ({name}) → provisioning…",
                          flush=True)
                    ok = pd.provision(serial, args.server, name, args.apk, key)
                    if ok:
                        provisioned.add(serial)
                        newly += 1
                        print(f"[auto] {serial}: OK — joined the matrix", flush=True)
                    else:
                        print(f"[auto] {serial}: provisioning incomplete — will "
                              f"retry next poll", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[auto] {serial}: error {exc}", flush=True)

            # Forget devices that have unplugged, so a re-plug re-checks them.
            live = set(safe(connected, [], "adb devices"))
            provisioned.intersection_update(live)
            if args.once:
                print(
                    f"[auto] scan done · {len(live)} device(s) connected · "
                    f"{newly} newly provisioned.",
                    flush=True,
                )
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[auto] stopping.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
