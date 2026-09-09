#!/usr/bin/env python3
"""
Zero-touch device provisioning — turn a freshly-wired phone into a running agent
with ONE command, no tapping on the phone. Non-root, verified on MIUI/Android 10.

Steps per device:
  1. install (or reinstall -r) the APK
  2. enable the accessibility service   (settings put secure …)
  3. add to battery whitelist           (dumpsys deviceidle whitelist +pkg)
  4. push server URL + device name and auto-start (am start with extras)

The only thing it can't set programmatically is MIUI's "自启动" toggle (no
non-root API) — the PC watchdog (agent_watchdog.py) covers relaunch instead.

Usage:
    python provision_device.py --server http://192.168.1.8:8010/api/v1 \
        --serial 6127f7f6 --name MI9-01
    python provision_device.py --server http://192.168.1.8:8010/api/v1 --all \
        --name-prefix 抖音机
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PKG = "com.example.douyinagent"
A11Y = f"{PKG}/{PKG}.accessibility.DouyinAccessibilityService"
MAIN = f"{PKG}/.MainActivity"
DEFAULT_APK = "android-agent/app/build/outputs/apk/debug/app-debug.apk"
# The backend persists its registration key here; auto-read it so provisioning
# keeps working once registration is locked (no open registration).
PROVISION_KEY_FILE = "backend/storage/.provision_key"


def _key_candidates() -> list[Path]:
    """Where the registration key can live. The repo path only exists on a dev
    box — in the packaged 迁移包 the tool runs as `程序\\autoprovision.exe` and the
    key sits in the SIBLING data dir (`数据\\storage`), which is exactly the
    layout the backend itself resolves. Getting this wrong is silent and fatal:
    provisioning proceeds, `POST /devices/register` rejects the keyless agent,
    and the phone shows「设备凭证失效」forever without ever reaching the console."""
    out: list[Path] = []
    home = os.environ.get("MATRIX_HOME", "").strip()
    if home:
        out.append(Path(home) / "storage" / ".provision_key")
    out.append(Path(PROVISION_KEY_FILE))  # repo layout (cwd = repo root)
    exe = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0] or ".")
    try:
        exe = exe.resolve()
    except OSError:
        pass
    prog = exe.parent            # …\程序
    pkg_root = prog.parent       # …\发布室-迁移包
    # 数据\storage is the real one (the backend runs with cwd=数据); a stray
    # 程序\storage from a wrong-cwd run may hold a DIFFERENT key — check it last.
    out += [
        pkg_root / "数据" / "storage" / ".provision_key",
        pkg_root / "data" / "storage" / ".provision_key",
        prog / "storage" / ".provision_key",
    ]
    return out


def persisted_provision_key(verbose: bool = False) -> str:
    direct = os.environ.get("MATRIX_PROVISION_KEY", "").strip()
    if direct:
        return direct
    for path in _key_candidates():
        try:
            key = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if key:
            if verbose:
                print(f"[provision] 注册密钥来自 {path}", flush=True)
            return key
    print("[provision] ⚠ 找不到 .provision_key —— 新设备注册会被后端拒绝", flush=True)
    return ""


def adb(args, serial=None, check=False):
    cmd = ["adb"] + (["-s", serial] if serial else []) + args
    r = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.strip()}")
    return (r.stdout or "") + (r.stderr or "")


def devices():
    out = adb(["devices"])
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]


def is_network(serial: str) -> bool:
    """云机是网络 adb（`192.168.1.200:6021`），USB 机是纯序列号。"""
    return ":" in serial


def installed(serial: str) -> bool:
    return PKG in adb(["shell", "pm", "list", "packages", PKG], serial)


def resumed_activity(serial: str) -> str:
    """用 `dumpsys activity activities` 的 ResumedActivity，**不要**用
    `dumpsys window | mCurrentFocus`：云机是多屏，后者会读到 Display-3 的 null。"""
    out = adb(["shell", "dumpsys", "activity", "activities"], serial)
    for line in out.splitlines():
        if "ResumedActivity" in line:
            return line.strip()
    return ""


# ColorOS 安装确认页（未经安全审核 → 继续安装）。安装进程会一直卡在这里，
# 没人点就是 Failure[-99]／永远装不上——这是新云机「检测不到」的直接原因之一。
_INSTALL_UI = ("installguideactivity", "packageinstalleractivity", "packageinstaller")
_CONFIRM_TEXTS = ("继续安装", "仍要安装", "安装")


def _install_dialog_up(serial: str) -> bool:
    r = resumed_activity(serial).lower()
    return any(hint in r for hint in _INSTALL_UI)


def _dump_ui(serial: str) -> str:
    adb(["shell", "uiautomator", "dump", "/sdcard/_inst.xml"], serial)
    return adb(["shell", "cat", "/sdcard/_inst.xml"], serial)


def _tap_bounds(serial: str, bounds) -> None:
    x1, y1, x2, y2 = (int(b) for b in bounds)
    adb(["shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2)], serial)


def tap_install_confirm(serial: str) -> bool:
    """按钮坐标随 ROM/布局变化，绝不能写死：dump 出弹窗再定位。两个坑：
    ① 云机一连上 adb 就会弹「USB 用于」系统面板，盖住安装按钮，而且 uiautomator
       只 dump 得到这个面板 —— 先按返回收掉它；
    ② ColorOS 的安装按钮**不进无障碍树**（只有一个空的
       confirm_bottom_button_layout 按钮条），按文字找不到 —— 退而点按钮条中心
       （这批云机是单按钮布局，中心就是「安装」，已实测有效）。"""
    xml = _dump_ui(serial)
    if "USB 用于" in xml or "usb_select" in xml:
        adb(["shell", "input", "keyevent", "4"], serial)
        time.sleep(2)
        xml = _dump_ui(serial)
    for label in _CONFIRM_TEXTS:
        for pattern in (
            rf'text="{label}"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            rf'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*?text="{label}"',
        ):
            m = re.search(pattern, xml)
            if m:
                _tap_bounds(serial, m.groups())
                return True
    m = re.search(
        r'resource-id="[^"]*confirm_bottom_button_layout"[^>]*?'
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    )
    if m:
        _tap_bounds(serial, m.groups())
        return True
    return False


def install_apk(serial: str, apk: str, timeout: int = 300) -> bool:
    """`adb install -r` + 自动确认安装弹窗。必须边装边看屏幕：install 会阻塞
    在弹窗上，所以用 Popen 起安装、循环 poll 弹窗并点「继续安装」。"""
    proc = subprocess.Popen(
        ["adb", "-s", serial, "install", "-r", apk],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    deadline = time.time() + timeout
    taps = 0
    while proc.poll() is None and time.time() < deadline:
        time.sleep(2)
        try:
            if _install_dialog_up(serial) and tap_install_confirm(serial):
                taps += 1
                print(f"       安装确认弹窗 → 已点「继续安装」({taps})", flush=True)
                time.sleep(2)
        except Exception:  # noqa: BLE001 — 弹窗探测失败不该中断安装
            pass
    if proc.poll() is None:
        proc.kill()
        print("       install 超时", flush=True)
    out = ""
    try:
        out = (proc.stdout.read() or "").strip() if proc.stdout else ""
    except Exception:  # noqa: BLE001
        pass
    last = out.splitlines()[-1] if out.splitlines() else ""
    ok = installed(serial)
    print(f"       {last or ('已安装' if ok else '安装失败')}", flush=True)
    return ok


def a11y_bound(serial: str) -> bool:
    """`Bound services` 只列 label 不列包名（踩过坑：按包名判断会误判全员未绑定
    → 全机房重启）。这些机器只装了我们一个无障碍服务，非空即已绑定。"""
    out = adb(["shell", "dumpsys", "accessibility"], serial)
    if "Bound services" not in out:
        return False
    return "Service[" in out.split("Bound services", 1)[1][:400]


def push_config(serial, server, name, provision_key=""):
    """把 base_url / 设备名 / 注册密钥写进 Agent 并自启。装过 Agent 但没注册上的
    机器（云机镜像克隆最常见）也要走这一步，否则它永远停在「设备凭证失效」。

    ⚠ Agent 已经在跑时，`am start` 只是把已有的 MainActivity 调到前台，
    **onCreate 不会重跑，extras 被直接丢掉** —— 云机6021 就是这样连推两轮配置
    都没生效。所以进程还活着就加 `-S`（先强杀再启动），让 onCreate 真正吃到。"""
    start_args = ["shell", "am", "start"]
    if adb(["shell", "pidof", PKG], serial).strip():
        start_args.append("-S")
    start_args += [
        "-n", MAIN,
        "-e", "base_url", server,
        "-e", "device_name", name,
        "--ez", "auto_start", "true",
    ]
    if provision_key:
        start_args += ["-e", "provision_key", provision_key]
    adb(start_args, serial)
    time.sleep(2)
    return adb(["shell", "pidof", PKG], serial).strip() != ""


def reboot_to_bind(serial, reverse_port=8010, wait=300):
    """新装的无障碍服务在 Android 13 上「设了但没绑定」，只有重启能让系统绑上。
    重启纪律：重启 → 等 boot_completed → 只重设 adb 层的东西（connect/reverse/
    竖屏）→ 然后什么都别做，让 BootReceiver 自己把 a11y 和前台服务拉起来。"""
    print("  [5/5] 无障碍未绑定 → 重启手机让系统绑定 …", flush=True)
    adb(["reboot"], serial)
    time.sleep(20)
    deadline = time.time() + wait
    booted = False
    while time.time() < deadline:
        if is_network(serial):
            subprocess.run(["adb", "connect", serial], capture_output=True,
                           text=True, errors="replace", timeout=20)
        if adb(["shell", "getprop", "sys.boot_completed"], serial).strip() == "1":
            booted = True
            break
        time.sleep(5)
    if not booted:
        print("       重启后没等到 boot_completed", flush=True)
        return False
    adb(["reverse", f"tcp:{reverse_port}", f"tcp:{reverse_port}"], serial)
    adb(["shell", "settings", "put", "system", "accelerometer_rotation", "0"], serial)
    adb(["shell", "settings", "put", "system", "user_rotation", "0"], serial)
    time.sleep(60)  # 别去 am start 催它，一催 onCreate 不跑、前台服务反而起不来
    bound = a11y_bound(serial)
    print(f"       重启后 a11y bound: {bound}", flush=True)
    return bound


def enable_accessibility(serial):
    # Append our service to whatever is already enabled (don't clobber others).
    cur = adb(["shell", "settings", "get", "secure", "enabled_accessibility_services"], serial).strip()
    cur = "" if cur in ("null", "") else cur
    parts = [p for p in cur.split(":") if p]
    if A11Y not in parts:
        parts.append(A11Y)
    adb(["shell", "settings", "put", "secure", "enabled_accessibility_services", ":".join(parts)], serial)
    adb(["shell", "settings", "put", "secure", "accessibility_enabled", "1"], serial)


def provision(serial, server, name, apk, provision_key="",
              reboot_if_unbound=False, reverse_port=8010):
    print(f"\n=== {serial} ({name}) ===", flush=True)
    if installed(serial):
        print("  [1/4] APK 已安装，跳过安装", flush=True)
    else:
        print("  [1/4] install APK （会自动确认安装弹窗）…", flush=True)
        if not install_apk(serial, apk):
            return False
    print("  [2/4] enable accessibility …", flush=True)
    enable_accessibility(serial)
    print("  [3/4] battery whitelist + overlay (bg-launch) …", flush=True)
    adb(["shell", "dumpsys", "deviceidle", "whitelist", f"+{PKG}"], serial)
    # Overlay appop exempts background activity starts, so the service can
    # relaunch Douyin even when our app isn't in front.
    adb(["shell", "appops", "set", PKG, "SYSTEM_ALERT_WINDOW", "allow"], serial)
    # 通知权限提前授掉：不授的话 MainActivity 一起来就弹运行时权限框，那个框会变成
    # 最顶层 Activity，后续任何 `am start ... -e base_url ...` 的 intent 都会被投给它、
    # 到不了我们的 Activity（am 只给一句 Warning，配置静默不生效）。
    adb(["shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS"], serial)
    print("  [4/4] push config + auto-start …", flush=True)
    alive = push_config(serial, server, name, provision_key)
    # 推配置时的 `-S` 会把无障碍解绑，所以绑定状态只有在这一步之后才算数；
    # 没绑上就再 toggle 一次（绑过的服务这样能绑回来，全新装的要重启）。
    bound = a11y_bound(serial)
    if not bound:
        enable_accessibility(serial)
        time.sleep(3)
        bound = a11y_bound(serial)
    print(f"  done. app running: {alive} · accessibility bound: {bound}", flush=True)
    if not bound:
        if reboot_if_unbound:
            reboot_to_bind(serial, reverse_port)
        else:
            print("       无障碍已设置但未绑定 —— 需要重启手机才会绑定，"
                  "编排器的 autoheal 线程会自动做", flush=True)
    return alive


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="backend base URL, e.g. http://192.168.1.8:8010/api/v1")
    ap.add_argument("--apk", default=DEFAULT_APK)
    ap.add_argument("--serial", help="provision one device")
    ap.add_argument("--all", action="store_true", help="provision every connected device")
    ap.add_argument("--name", help="device name (single device)")
    ap.add_argument("--name-prefix", default="抖音机", help="name prefix when using --all")
    ap.add_argument(
        "--provision-key",
        default=None,
        help="pre-shared key; defaults to backend/storage/.provision_key",
    )
    ap.add_argument(
        "--reboot-if-unbound",
        action="store_true",
        help="无障碍没绑定就重启手机（Android 13 上必须重启才会绑定）",
    )
    args = ap.parse_args()
    if args.provision_key is None:
        args.provision_key = persisted_provision_key(verbose=True)

    targets = []
    if args.all:
        for i, s in enumerate(devices(), 1):
            targets.append((s, f"{args.name_prefix}-{i:02d}"))
    elif args.serial:
        targets.append((args.serial, args.name or args.serial))
    else:
        ds = devices()
        if len(ds) != 1:
            print("Multiple/zero devices — use --serial or --all.", file=sys.stderr)
            return 2
        targets.append((ds[0], args.name or ds[0]))

    ok = 0
    for serial, name in targets:
        try:
            if provision(serial, args.server, name, args.apk, args.provision_key,
                         reboot_if_unbound=args.reboot_if_unbound):
                ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", flush=True)
    print(f"\n[provision] {ok}/{len(targets)} device(s) ready.", flush=True)
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
