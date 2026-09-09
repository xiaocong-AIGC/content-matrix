#!/usr/bin/env python3
"""
PC-side app-switch orchestrator — the reliable half of the "division of labour".

On MIUI the on-phone agent can't reliably bring Douyin (or itself) to the front
from the background. adb CAN (`am start` runs as shell, exempt from MIUI's
background-popup throttle). So the agent just emits a one-line logcat signal and
this script does the actual switch over adb:

    logcat  AgentSwitch: douyin   ->  adb am start <douyin launcher>
    logcat  AgentSwitch: home     ->  adb am start <agent MainActivity>

The on-phone accessibility service still does ALL the in-Douyin tapping/typing;
this only handles the cross-app switches that MIUI blocks.

Runs one watcher thread per connected device, so it scales to a USB-hub matrix.

Usage:
    python agent_orchestrator.py                 # all connected devices
    python agent_orchestrator.py --serial 6127f7f6
"""
import argparse
import os
import subprocess
import sys
import threading
import time

PKG = "com.example.douyinagent"
MAIN = f"{PKG}/.MainActivity"
# Short-form id (what `ime list` reports); the full-component form is rejected
# by `ime enable/set` as "Unknown input method".
IME = f"{PKG}/.accessibility.AgentInputMethodService"
DOUYIN = "com.ss.android.ugc.aweme"
XHS = "com.xingin.xhs"


AGENT_PKG = "com.example.douyinagent"
AGENT_A11Y = f"{AGENT_PKG}/{AGENT_PKG}.accessibility.DouyinAccessibilityService"


def adb(serial, args):
    return subprocess.run(
        ["adb", "-s", serial] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    ).stdout


def _block(out, key):
    """The `{...}` block following `key` in dumpsys text (else a short slice)."""
    i = out.find(key)
    if i == -1:
        return ""
    j = out.find("}", i)
    return out[i:j + 1] if j != -1 else out[i:i + 300]


def _a11y_state(serial):
    """(enabled, bound) for our accessibility service on this phone.
    enabled = the OS is configured to run it; bound = it's actually connected.
    On A13/ColorOS the service is often enabled-but-NOT-bound after boot — the only
    reliable fix is a reboot, so we detect that here and auto-reboot (see autoheal)."""
    out = adb(serial, ["shell", "dumpsys", "accessibility"]) or ""
    if AGENT_PKG not in out:
        return (False, False)  # agent not installed / not this kind of device
    # `Enabled services` lists services by PACKAGE (…{{com.example.douyinagent/…}}),
    # but `Bound services` lists them by LABEL only (…{Service[label=抖音发布 Agent …]}),
    # NOT the package name. So bound = the block has a Service entry (non-empty {}) —
    # these phones only ever run OUR a11y service. (Checking AGENT_PKG in the bound
    # block was the bug that made every device look unbound → mass-reboot.)
    enabled = AGENT_PKG in _block(out, "Enabled services")
    bound = "Service[" in _block(out, "Bound services")
    return (enabled, bound)


def _autoheal_log(msg):
    """Audit trail for auto-heal actions: stdout (captured by the app) + a rolling
    file so the operator can see exactly which phones were auto-rebooted and when."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [autoheal] {msg}"
    print("[orch] " + msg, flush=True)
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])),
                               "autoheal.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _post_reboot(serial):
    """After an auto-reboot: wait for boot, reconnect (network adb drops), and
    re-assert the reverse tunnel + portrait. Then LEAVE IT ALONE — BootReceiver
    binds a11y + starts the foreground service on its own (A13 discipline)."""
    for _ in range(36):  # up to ~3min
        time.sleep(5)
        subprocess.run(["adb", "connect", serial], capture_output=True,
                       text=True, errors="replace", timeout=15)
        if (adb(serial, ["shell", "getprop", "sys.boot_completed"]) or "").strip() == "1":
            adb(serial, ["reverse", "tcp:8010", "tcp:8010"])
            adb(serial, ["shell", "settings", "put", "system", "user_rotation", "0"])
            print(f"[orch] {serial}: rebooted + reverse re-set (a11y will bind on boot)",
                  flush=True)
            return


def _reassert_a11y(serial):
    """我们的无障碍服务被人从 `enabled_accessibility_services` 里挤掉时，追加回去。

    🔴 抢这个位置的**不是别的脚本，是抖音自己**：云机6001 实测被写成
    `com.ss.android.ugc.aweme/.live.livehostimpl.AudioAccessibilityService`
    一个 —— 我们的服务整个不见了。抖音是**覆盖式**写这个设置的，
    而写它会让系统重启列表里的**所有**无障碍服务，所以这同时也是
    「accessibility_stopped 每 3~4 秒翻一次」的来源。

    我们**追加**而不是覆盖（别把抖音的踢掉，那会引发互相踢的死循环）。
    返回 True 表示做了修复。
    """
    cur = (adb(serial, ["shell", "settings", "get", "secure",
                        "enabled_accessibility_services"]) or "").strip()
    cur = "" if cur in ("null", "") else cur
    parts = [p for p in cur.split(":") if p]
    if AGENT_A11Y in parts:
        return False
    parts.append(AGENT_A11Y)
    adb(serial, ["shell", "settings", "put", "secure",
                 "enabled_accessibility_services", ":".join(parts)])
    adb(serial, ["shell", "settings", "put", "secure", "accessibility_enabled", "1"])
    _autoheal_log(
        f"{serial}: 无障碍被挤出列表（当时是 [{cur or '空'}]），已把我们的服务追加回去"
    )
    return True


def _rebind_a11y(serial):
    """「已启用但没绑定」时，把设置**置空再原样写回**，逼系统重新绑定。

    这是从 autoheal.log 里逼出来的：这个状态天天在发生（6004/6008/6009/6010/
    6013/6018/6019/6022/6025…一天好几次），而原来唯一的处置是「告警，等人工重启
    手机」—— 没人会去重启，于是那台手机就一直白站着。

    实测（6013、6019，2026-09-08）：置空再写回就能重新绑上，**不用重启**。
    原样写回而不是只写我们自己的，是为了别把抖音的服务踢掉
    （抖音会写这个设置，互相踢会变成死循环，见 `_reassert_a11y`）。

    返回 True 表示重新绑上了。
    """
    cur = (adb(serial, ["shell", "settings", "get", "secure",
                        "enabled_accessibility_services"]) or "").strip()
    cur = "" if cur in ("null", "") else cur
    parts = [p for p in cur.split(":") if p]
    if AGENT_A11Y not in parts:
        parts.append(AGENT_A11Y)
    adb(serial, ["shell", "settings", "put", "secure",
                 "enabled_accessibility_services", '""'])
    time.sleep(2)
    adb(serial, ["shell", "settings", "put", "secure",
                 "enabled_accessibility_services", ":".join(parts)])
    adb(serial, ["shell", "settings", "put", "secure", "accessibility_enabled", "1"])
    time.sleep(5)
    _, bound = _a11y_state(serial)
    _autoheal_log(
        f"{serial}: 无障碍未绑定 → 置空重写"
        + ("，已重新绑上" if bound else "，仍未绑上，需要人工重启这台手机")
    )
    return bound


def autoheal(stop, watched):
    """Watch for phones whose a11y service is enabled-but-unbound. **Default = ALERT
    ONLY** (`_autoheal_log` once per phone) — we do NOT auto-reboot, because a reboot
    also cuts off any OTHER script using that phone (co-tenant on the USB box). The
    dashboard already shows these as 橙色「需重启」via accessibility_ok; the operator
    reboots manually when it won't disturb their other work. Set env
    `MATRIX_AUTOHEAL_REBOOT=1` to opt back into the old auto-reboot behaviour."""
    unbound_since: dict[str, float] = {}
    last_rebind: dict[str, float] = {}   # 同一台手机 5 分钟内最多重绑一次
    last_reboot: dict[str, float] = {}
    reboot_count: dict[str, int] = {}  # give up after MAX_ATTEMPTS (avoid loops)
    alerted: set[str] = set()          # log the "needs reboot" alert once per phone
    fleet_last_reboot = [0.0]          # GLOBAL guardrail: stagger reboots fleet-wide
    AUTO_REBOOT = os.environ.get("MATRIX_AUTOHEAL_REBOOT", "0") == "1"
    UNBOUND_GRACE = 120     # must be stuck-unbound this long before we act
    REBIND_COOLDOWN = 300   # 重绑本身会重启列表里的所有服务，别频繁做
    COOLDOWN = 900          # never reboot the same phone more than once / 15min
    MAX_ATTEMPTS = 2        # if 2 reboots don't bind it, stop → needs manual attention
    FLEET_GAP = 90          # at most ONE reboot every 90s across the WHOLE fleet
    while not stop.is_set():
        for _ in range(12):  # ~60s cycle, but wake fast on stop
            if stop.is_set():
                return
            time.sleep(5)
        for serial in list(watched):
            try:
                enabled, bound = _a11y_state(serial)
                now = time.time()
                if not enabled:
                    # 没在列表里 —— 大概率是被抖音覆盖掉了。追加回去，
                    # 下一轮就会看到 bound，不需要重启手机。
                    if _reassert_a11y(serial):
                        unbound_since.pop(serial, None)
                        alerted.discard(serial)
                    continue
                if enabled and not bound:
                    first = unbound_since.setdefault(serial, now)
                    if now - first < UNBOUND_GRACE:
                        continue  # give boot/settle time before flagging
                    # ① 先试「置空再写回」—— 实测能重新绑上，不用重启手机，
                    #    也不会打断任何东西（服务没绑，这台手机本来就干不了活）。
                    if now - last_rebind.get(serial, 0) >= REBIND_COOLDOWN:
                        last_rebind[serial] = now
                        if _rebind_a11y(serial):
                            unbound_since.pop(serial, None)
                            alerted.discard(serial)
                            continue
                    # ② 重绑也没用，才告警等人工重启。
                    if serial not in alerted:
                        _autoheal_log(f"{serial}: 无障碍已启用但未绑定，重绑无效，需人工重启该手机")
                        alerted.add(serial)
                    if not AUTO_REBOOT:
                        continue
                    # --- opt-in auto-reboot (MATRIX_AUTOHEAL_REBOOT=1) ---
                    if reboot_count.get(serial, 0) >= MAX_ATTEMPTS:
                        continue
                    if now - fleet_last_reboot[0] < FLEET_GAP:
                        continue
                    if now - last_reboot.get(serial, 0) >= COOLDOWN:
                        n = reboot_count.get(serial, 0) + 1
                        _autoheal_log(f"{serial}: 无障碍未绑定 → 自动重启 (第{n}/{MAX_ATTEMPTS}次)")
                        adb(serial, ["reboot"])
                        last_reboot[serial] = now
                        fleet_last_reboot[0] = now
                        reboot_count[serial] = n
                        unbound_since.pop(serial, None)
                        threading.Thread(target=_post_reboot, args=(serial,),
                                         daemon=True).start()
                        break
                else:
                    # Healthy (bound) → clear history so a FUTURE unbind is fresh.
                    unbound_since.pop(serial, None)
                    reboot_count.pop(serial, None)
                    last_rebind.pop(serial, None)
                    alerted.discard(serial)
            except Exception:
                pass


def devices():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]


def _endpoints_from_backend():
    """The first-run wizard writes the cloud-phone adb地址 into the backend; read
    it so a fresh install doesn't need the env var. Best-effort (backend may be
    starting up). Empty/unreachable → fall back to the env/default."""
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(
            "http://127.0.0.1:8010/api/v1/agent/adb-endpoints", timeout=3
        ) as r:
            return json.load(r).get("endpoints", "") or ""
    except Exception:
        return ""


def _network_endpoints():
    """Cloud-phone adb endpoints to auto-`adb connect` (network adb drops off on
    every adb-server restart). Priority: backend (first-run wizard) → env
    MATRIX_ADB_ENDPOINTS → built-in default. Comma-separated, port ranges allowed
    ("192.168.1.200:6001-6020, 1.2.3.4:5555")."""
    spec = (
        _endpoints_from_backend()
        or os.environ.get("MATRIX_ADB_ENDPOINTS", "").strip()
    )
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


def connect_network_endpoints(endpoints):
    """`adb connect` any configured network endpoint not already connected."""
    if not endpoints:
        return
    connected = set(devices())
    for ep in endpoints:
        if ep not in connected:
            subprocess.run(["adb", "connect", ep], capture_output=True,
                           text=True, errors="replace", timeout=15)


def launch_app(serial, pkg, cold=False):
    """把目标 App 拉到前台。`cold=True` 时先 force-stop 再拉起。

    ⚠ 只发 launcher intent 是不够的：那是"恢复"不是"打开"。抖音上次停在哪一页，
    恢复回来还是那一页。实测（云机 6019）：
      只发 launcher intent  → 停在「我」的个人主页（我的订单/我的钱包/作品/日常…）
      force-stop 之后再拉起 → 首页推荐信息流（直播/团购/同城/关注）
    页面识别器只认得后者，于是前者会一路 UNKNOWN → 自动恢复 → 恢复不出来 →
    卡到超时（任务 #1595 就是这样空转了 6 分钟才被放弃）。

    force-stop 需要 shell 权限，App 自己做不到，只能由这边做 —— 这正是
    "分工"的意义。实测 6002/6003/6019 冷启动后登录态都在，不会掉登录。
    """
    if cold:
        adb(serial, ["shell", "am", "force-stop", pkg])
        time.sleep(0.6)
    adb(serial, ["shell", "monkey", "-p", pkg, "-c",
                 "android.intent.category.LAUNCHER", "1"])


def go_home_app(serial):
    adb(serial, ["shell", "am", "start", "-n", MAIN])


_saved_ime = {}  # serial -> the user's IME, to restore after typing topics


def set_our_ime(serial):
    """Switch to our in-app IME (the one privileged step that needs shell). The
    AGENT then binds the 正文 to it and drives the whole topic loop itself —
    compose/find-suggestion/tap/trim — using the live a11y tree (no flaky
    uiautomator dump). Triggered by the agent's `AgentImeSet` logcat signal."""
    original = adb(serial, ["shell", "settings", "get", "secure",
                            "default_input_method"]).strip()
    if original and original != "null" and IME not in original:
        _saved_ime[serial] = original
    adb(serial, ["shell", "ime", "enable", IME])
    adb(serial, ["shell", "ime", "set", IME])
    print(f"[orch] {serial}: IME -> Agent 输入法", flush=True)


def restore_ime(serial):
    """Restore the user's keyboard after the agent finishes the topics (its
    `AgentImeRestore` signal)."""
    original = _saved_ime.pop(serial, None)
    if original:
        adb(serial, ["shell", "ime", "set", original])
        print(f"[orch] {serial}: IME restored -> {original}", flush=True)


def watch(serial, stop, watched=None):
    print(f"[orch] watching {serial}", flush=True)
    # Clear backlog so we only react to fresh signals.
    subprocess.run(["adb", "-s", serial, "logcat", "-c"],
                   capture_output=True, text=True)
    proc = subprocess.Popen(
        ["adb", "-s", serial, "logcat", "-s",
         "AgentSwitch:I", "AgentImeSet:I", "AgentImeRestore:I", "AgentInput:I"],
        stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    last = (None, 0.0)
    try:
        for line in proc.stdout:
            if stop.is_set():
                break
            # 话题 flow: switch to our IME on AgentImeSet (the agent then drives the
            # topic loop), restore the user's keyboard on AgentImeRestore.
            if "AgentImeSet:" in line:
                set_our_ime(serial)
                continue
            if "AgentImeRestore:" in line:
                restore_ime(serial)
                continue
            # @所有人 flow: the agent can't inject a real keystroke into 抖音 (a11y
            # setText doesn't trigger the @-picker). It emits `AgentInput: @` and we
            # type it via `adb shell input text`, which DOES pop the member picker.
            if "AgentInput:" in line:
                payload = line.split("AgentInput:", 1)[1].strip()
                if payload:
                    # input text needs spaces as %s; we only inject short tokens.
                    adb(serial, ["shell", "input", "text", payload.replace(" ", "%s")])
                continue
            low = line.strip().lower()
            # `douyin:cold` = 发布任务要开始了，**先 force-stop 再拉起**。
            # 裸的 `douyin` = 更新数据/读账号那条路径，那边拉起后只 sleep 3.5 秒
            # 就去点「我」，冷启动来不及，所以维持"恢复"语义不动。
            cold = low.endswith(":cold")
            if cold:
                low = low[: -len(":cold")]
            if low.endswith("douyin"):
                target = "douyin"
            elif low.endswith("xhs"):
                target = "xhs"
            elif low.endswith("home"):
                target = "home"
            else:
                continue
            # Debounce duplicate signals within 2s.
            now = time.time()
            if last[0] == (target, cold) and now - last[1] < 2:
                continue
            last = ((target, cold), now)
            if target == "douyin":
                print(f"[orch] {serial}: -> 抖音{'（冷启动）' if cold else ''}", flush=True)
                launch_app(serial, DOUYIN, cold=cold)
            elif target == "xhs":
                print(f"[orch] {serial}: -> 小红书{'（冷启动）' if cold else ''}", flush=True)
                launch_app(serial, XHS, cold=cold)
            else:
                print(f"[orch] {serial}: -> Agent", flush=True)
                go_home_app(serial)
    finally:
        proc.terminate()
        # Let the supervisor re-spawn a watcher if this device reconnects.
        if watched is not None:
            watched.discard(serial)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", help="one device; default = all connected")
    args = ap.parse_args()

    stop = threading.Event()
    watched: set[str] = set()

    def ensure_watched(serial):
        if serial not in watched:
            watched.add(serial)
            threading.Thread(
                target=watch, args=(serial, stop, watched), daemon=True
            ).start()
            print(f"[orch] + device {serial}", flush=True)

    endpoints = _network_endpoints()
    if endpoints:
        print(f"[orch] auto-connecting {len(endpoints)} network endpoint(s)", flush=True)
    # Self-healing: auto-reboot any phone stuck a11y-unbound (no human needed).
    threading.Thread(target=autoheal, args=(stop, watched), daemon=True).start()
    print("[orch] orchestrating + a11y auto-heal (dynamic device discovery). Ctrl-C to stop.",
          flush=True)
    try:
        cycle = 0
        while True:
            # Re-read endpoints every ~30s so the first-run wizard's 云机地址 takes
            # effect without restarting the orchestrator.
            cycle += 1
            if cycle % 10 == 0:
                fresh = _network_endpoints()
                if fresh:
                    endpoints = fresh
            # Network-adb cloud phones drop off on adb-server restart → reconnect
            # the configured endpoints each cycle before scanning.
            connect_network_endpoints(endpoints)
            # Re-scan so devices that connect LATER (matrix hot-plug, reconnect
            # with a new serial) are picked up — not just the ones present at start.
            for serial in ([args.serial] if args.serial else devices()):
                ensure_watched(serial)
            time.sleep(3)
    except KeyboardInterrupt:
        stop.set()
        print("\n[orch] stopping.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
