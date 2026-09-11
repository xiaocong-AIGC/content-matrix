import json
from datetime import timezone

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header
from sqlmodel import Session, func, select

from app.core.config import get_settings
from app.core.serialization import normalize_datetimes
from app.db.session import get_session
from app.core.enums import DeviceStatus, TERMINAL_STATUSES
from app.models.entities import ChatGroup, Device, DeviceAccount, PublishTask, utcnow
from fastapi import HTTPException

from app.schemas.dto import (
    BatchScheduleRequest,
    DeviceAutoBroadcastUpdate,
    DeviceAutoPublishUpdate,
    DeviceHealthUpdate,
    DeviceHeartbeat,
    DeviceProfileUpdate,
    DeviceRegister,
    SchedulePostsRequest,
)
from app.core.admin_auth import require_admin, require_provision_key
from app.services.city_policy import (
    OVERRIDE_OFF,
    OVERRIDE_ON,
    policies_by_city,
    policy_for,
    publishes,
    target_for,
)
from app.services.accounts import (
    accounts_for,
    get_account,
    serialize_account,
    upsert_account,
)
from app.services.device_auth import authenticate_device, issue_device_token
from app.services.tasks import count_today_published, schedule_posts

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", dependencies=[Depends(require_provision_key)])
def register(payload: DeviceRegister, session: Session = Depends(get_session)):
    device = session.exec(
        select(Device).where(Device.device_code == payload.device_code)
    ).first()
    if not device:
        device = Device(device_code=payload.device_code, name=payload.name)
    raw_token, token_hash = issue_device_token()
    device.name = payload.name
    device.platform = payload.platform
    device.agent_version = payload.agent_version
    device.android_version = payload.android_version
    device.douyin_version = payload.douyin_version
    if payload.douyin_nickname:
        device.douyin_nickname = payload.douyin_nickname
    if payload.douyin_id:
        device.douyin_id = payload.douyin_id
    device.capabilities_json = json.dumps(payload.capabilities, ensure_ascii=False)
    device.token_hash = token_hash
    device.status = "online"
    device.last_heartbeat_at = utcnow()
    device.updated_at = utcnow()
    session.add(device)
    session.commit()
    session.refresh(device)
    # Snapshot the response BEFORE the account upsert — its commit would expire
    # the `device` object and blank out model_dump().
    result = {**device.model_dump(), "capabilities": payload.capabilities}
    result.pop("token_hash", None)
    result["agent_token"] = raw_token
    # Ensure a douyin account row exists (new model). Identity from the register
    # payload if present; operator knobs (city/quota/...) preserved.
    upsert_account(
        session, device.id, "douyin",
        nickname=payload.douyin_nickname, account_id=payload.douyin_id,
    )
    return result


@router.post("/{device_id}/heartbeat")
def heartbeat(
    device_id: int,
    payload: DeviceHeartbeat,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    device = authenticate_device(session.get(Device, device_id), x_agent_token)
    # Don't let a heartbeat re-bind the device to a task that's already terminal
    # (e.g. the server auto-failed a stuck waiting_confirmation task while the
    # agent is still on it) — otherwise the device never frees and the queue
    # stays blocked. The agent drops the task on its next 409'd lease renewal.
    reported_task = payload.current_task_id
    if reported_task is not None:
        t = session.get(PublishTask, reported_task)
        if t is None or t.status in TERMINAL_STATUSES:
            reported_task = None
    device.status = payload.status if reported_task is not None else DeviceStatus.ONLINE
    device.current_task_id = reported_task
    if payload.accessibility_ok is not None:
        # 只在**翻转**时改时间戳：掉的时候记下起点，恢复时清掉。
        # 每次心跳都刷新的话，"掉了多久"永远是 0，那个数就白记了。
        if payload.accessibility_ok is False and device.accessibility_ok is not False:
            device.accessibility_since = utcnow()
        elif payload.accessibility_ok is True:
            device.accessibility_since = None
        device.accessibility_ok = payload.accessibility_ok
    device.agent_version = payload.agent_version or device.agent_version
    device.douyin_version = payload.douyin_version or device.douyin_version
    device.douyin_nickname = payload.douyin_nickname or device.douyin_nickname
    device.douyin_id = payload.douyin_id or device.douyin_id
    if payload.health:
        device.health = payload.health
        device.health_message = payload.health_message
    device.last_heartbeat_at = utcnow()
    device.updated_at = utcnow()
    session.add(device)
    session.commit()
    # Health applies to the specific platform account (per-platform health).
    if payload.health:
        acc = get_account(session, device_id, payload.platform)
        acc.health = payload.health
        acc.health_message = payload.health_message
        acc.updated_at = utcnow()
        session.add(acc)
        session.commit()
    # Keep the douyin account row's identity fresh from heartbeats.
    if payload.douyin_nickname or payload.douyin_id:
        upsert_account(
            session, device_id, "douyin",
            nickname=payload.douyin_nickname, account_id=payload.douyin_id,
        )
    # Hand the agent the on-demand 回采 request (manual "更新数据") and consume it,
    # so it fires once per click rather than looping.
    refresh_metrics = bool(device.metrics_refresh_requested)
    refresh_account = bool(device.account_refresh_requested)
    if refresh_metrics or refresh_account:
        device.metrics_refresh_requested = False
        device.account_refresh_requested = False
        session.add(device)
        session.commit()
    return {
        "status": "ok",
        "refresh_account": refresh_account,
        "server_time": utcnow(),
        "refresh_metrics": refresh_metrics,
    }


@router.post("/detect", dependencies=[Depends(require_admin)])
def detect_devices():
    """「检测设备」(账号矩阵首页按钮): scan adb ONCE, auto-provision any newly
    connected phone (install agent → accessibility → tunnel → register), and let
    its account auto-sync. Runs the proven `auto_provision.py --once` flow on the
    PC (the backend host, which has adb) instead of a constantly-polling daemon."""
    import re
    import subprocess
    import sys
    from pathlib import Path

    # 打包后 sys.executable 是 backend.exe，不是 python —— 直接拿它去跑
    # `tools/auto_provision.py` 会启动第二个后端实例（PyInstaller 忽略 argv），
    # 8010 端口被占 → 立刻退出 → 按钮永远返回「0 台」。所以冻结版必须调用
    # 同目录下冻结好的 autoprovision.exe。
    if getattr(sys, "frozen", False):
        prog = Path(sys.executable).parent
        exe = prog / "autoprovision.exe"
        if not exe.exists():
            raise HTTPException(
                status_code=500,
                detail=f"检测失败：找不到 {exe.name}（安装包不完整，请重新部署迁移包）",
            )
        cmd = [str(exe), "--once", "--server", "http://127.0.0.1:8010/api/v1"]
        apk = prog / "platform-tools" / "app-debug.apk"
        if apk.exists():
            cmd += ["--apk", str(apk)]
        cwd = str(prog)
    else:
        cmd = [
            sys.executable, "tools/auto_provision.py", "--once",
            "--server", "http://127.0.0.1:8010/api/v1",
        ]
        cwd = str(Path(__file__).resolve().parents[5])
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            # 一次扩容可能要装十几台机器（每台还要点安装弹窗），240s 根本不够
            encoding="utf-8", errors="replace", timeout=900,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="检测超时：设备较多时装机耗时较长，稍后刷新设备列表即可",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"检测失败：{exc}")
    output = (proc.stdout or "") + (proc.stderr or "")
    conn = re.search(r"(\d+) device\(s\) connected", output)
    newly = re.search(r"(\d+) newly provisioned", output)
    return {
        "ok": proc.returncode == 0,
        "connected": int(conn.group(1)) if conn else 0,
        "newly_provisioned": int(newly.group(1)) if newly else 0,
        "output": output[-2000:],
    }


def _is_online(device: Device) -> bool:
    """Same rule the device list uses: no heartbeat within the offline window = 离线."""
    hb = device.last_heartbeat_at
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    return (utcnow() - hb).total_seconds() <= get_settings().device_offline_seconds


@router.post("/refresh-metrics", dependencies=[Depends(require_admin)])
def request_metrics_refresh(session: Session = Depends(get_session)):
    """Operator hit 「更新数据」 on 效果榜 — flag the devices worth 回采 to re-run it on
    their next idle heartbeat. ONLY 在线 + 有已登录账号 devices: an offline phone can't
    collect, and a logged-out account has nothing to回采 — flagging them just wastes
    a heartbeat and inflates the "已通知 N 台" count. "已登录" mirrors the 在用 rule
    (logged_in is not False, so未探测的 None 也算,避免误藏)。"""
    # device_ids that have ≥1 account which is logged in (or not yet probed).
    live_dev_ids = {
        a.device_id
        for a in session.exec(select(DeviceAccount)).all()
        if a.logged_in is not False
    }
    flagged = 0
    for device in session.exec(select(Device)).all():
        if _is_online(device) and device.id in live_dev_ids:
            device.metrics_refresh_requested = True
            device.updated_at = utcnow()
            session.add(device)
            flagged += 1
    session.commit()
    return {"status": "ok", "devices": flagged}


@router.post("/{device_id}/refresh-account", dependencies=[Depends(require_admin)])
def request_account_refresh(device_id: int, session: Session = Depends(get_session)):
    """设备卡片「更新账号」— flag THIS device to force a fresh account read (and re-scan
    its 群) on its next idle heartbeat (picks up an account switch). Publishing is
    untouched. Offline devices are rejected — they can't re-read anything."""
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not _is_online(device):
        raise HTTPException(status_code=400, detail="设备离线，无法更新账号（请先让设备上线）")
    device.account_refresh_requested = True
    device.updated_at = utcnow()
    session.add(device)
    session.commit()
    return {"status": "ok"}


def serialize_device(session: Session, device: Device) -> dict:
    """Device dict + the primary (douyin) account's fields mirrored up to the
    legacy device-level keys (so the current frontend keeps working), plus the
    full `accounts` list for the new per-account UI."""
    accts = accounts_for(session, device.id)
    primary = next((a for a in accts if a.platform == "douyin"), None)
    data = {k: v for k, v in device.model_dump().items() if k != "token_hash"}
    data["capabilities"] = json.loads(device.capabilities_json)
    if primary:
        data["douyin_nickname"] = primary.nickname
        data["douyin_id"] = primary.account_id
        data["city"] = primary.city
        # 显示「实际发不发」，不是老开关 —— 城市开着时老开关可能是 false，
        # 卡片上显示「关」而它其实在发，正是城市策略要消灭的那种不一致。
        data["auto_publish"] = publishes(session, primary)
        data["daily_quota"] = primary.daily_quota
        data["health"] = primary.health
        data["health_message"] = primary.health_message
    data["today_published"] = count_today_published(session, device.id, "douyin")
    accounts = []
    pmap = policies_by_city(session)
    for a in accts:
        ad = serialize_account(a)
        ad["today_published"] = count_today_published(session, device.id, a.platform)
        # ⚠ `auto_publish` 这个字段名下发的是**实际发不发**，不是老开关原值。
        # 桌面版 app.exe 编译的是老前端，它的列表、计数、「全部关闭」全靠这个
        # 字段 —— 下发原值的话，城市开着的号在老界面上显示「不参与」，而
        # 「全部关闭」只会去关原值为 true 的号，其余照发，界面却说已全部关闭。
        # 账号总览「开着自动发布的号一定要看得见」那条判断也读这个字段。
        effective = publishes(session, a, pmap)
        ad["auto_publish"] = effective
        ad["publishes"] = effective
        ad["auto_publish_raw"] = a.auto_publish
        # 这个号今天实际该发几篇，后端算好 —— 前端各页面自己算的话，
        # 城市篇数、全局篇数、单号上限三者怎么组合，迟早有一页算错。
        ad["target"] = target_for(session, a, pmap)
        accounts.append(ad)
    data["accounts"] = accounts
    return data


@router.patch("/{device_id}/profile", dependencies=[Depends(require_admin)])
def set_profile(
    device_id: int,
    payload: DeviceProfileUpdate,
    session: Session = Depends(get_session),
):
    """Set an account's city (matrix grouping) and/or the device display name."""
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if payload.name is not None and payload.name.strip():
        device.name = payload.name.strip()
        device.updated_at = utcnow()
        session.add(device)
        session.commit()
    if payload.city is not None:
        acc = get_account(session, device_id, payload.platform)
        new_city = payload.city.strip() or "未分组"
        # ⚠ 城市真的变了就清掉账号例外。例外是「相对原来那个城市」设的，搬到
        # 新城市还带着它没有意义；而且搬进「未分组」之后，新页面的「单个号」
        # 列表不显示未分组的号 —— 这条例外就没有任何界面能看见、能改了，
        # 号会被一条看不见的「单独发」一直顶着往抖音发。
        if new_city != acc.city:
            acc.publish_override = None
        acc.city = new_city
        acc.updated_at = utcnow()
        session.add(acc)
        session.commit()
    session.refresh(device)
    return normalize_datetimes(serialize_device(session, device))


@router.patch("/{device_id}/auto-publish", dependencies=[Depends(require_admin)])
def set_auto_publish(
    device_id: int,
    payload: DeviceAutoPublishUpdate,
    session: Session = Depends(get_session),
):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    acc = get_account(session, device_id, payload.platform)
    acc.auto_publish = payload.auto_publish
    # ⚠ 城市设了开关之后，老开关就不起作用了（城市优先）。如果只写老开关，
    # 这个按钮会**返回成功、但什么都没变** —— 静默失败。桌面版 app.exe 里
    # 编译的还是老前端，运营点它就会撞上。
    # 所以把这次点击翻译成账号例外：和城市不一致就设例外，一致就清掉例外
    # （跟着城市走就好，不留一条冗余的例外）。老按钮的意图因此永远成立。
    policy = policy_for(session, acc.city)
    if policy is not None and policy.auto_publish is not None:
        acc.publish_override = (
            None if payload.auto_publish == policy.auto_publish
            else (OVERRIDE_ON if payload.auto_publish else OVERRIDE_OFF)
        )
    else:
        # 城市没设开关（未分组、折算之后才出现的新城市）时，老开关自己就是
        # 最终决定。残留的例外必须清掉 —— 否则 publishes() 先看例外，老按钮
        # 点关返回 200、号却还被一条「单独发」顶着一直发。
        acc.publish_override = None
    if payload.daily_quota is not None:
        acc.daily_quota = payload.daily_quota
    acc.updated_at = utcnow()
    session.add(acc)
    session.commit()
    session.refresh(device)
    return normalize_datetimes(serialize_device(session, device))


class PublishOverrideIn(BaseModel):
    """账号例外。null = 跟随城市（默认），"on" = 强制开，"off" = 强制关。"""

    override: str | None = None
    platform: str = "douyin"


@router.patch("/{device_id}/publish-override", dependencies=[Depends(require_admin)])
def set_publish_override(
    device_id: int,
    payload: PublishOverrideIn,
    session: Session = Depends(get_session),
):
    """给单个号设例外，不用为了一个号去动整个城市。

    典型用法：全城开着，这个号今天被限流了，先强制关它一天。
    """
    from app.services.city_policy import OVERRIDES

    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    value = (payload.override or "").strip() or None
    if value is not None and value not in OVERRIDES:
        raise HTTPException(status_code=400, detail="只能是 on、off 或留空（跟随城市）")
    acc = get_account(session, device_id, payload.platform)
    acc.publish_override = value
    acc.updated_at = utcnow()
    session.add(acc)
    session.commit()
    session.refresh(device)
    return normalize_datetimes(serialize_device(session, device))


@router.get("/roster", dependencies=[Depends(require_admin)])
def account_roster(session: Session = Depends(get_session)):
    """**「在跑的号」只有这一个定义。**

    以前这个判断被各写各的：首页 KPI 对全部 37 台设备的所有账号求和（分母 44）、
    企微群通知用「最近 48 小时发过」（14）、批量排期弹窗又是另一套。同一件事三个数，
    而运营两边都看 —— 群里说 14 个号、首页说 /44，他不知道该信谁。

    收口到后端的好处不只是"少写一遍"：这个定义会随业务变（比如以后加平台维度），
    改一处就全站一致，而不是改完还得去翻哪个页面漏了。

    口径就是通知那套：**最近 48 小时成功发过作品的号**。不能用「在线」（心跳一断
    目标就跳水，运营会看到数字分钟级晃动），也不能用 auto_publish
    （线上 37 个账号这个开关全是 false，用它算分母会得到 0）。
    """
    from app.services.city_policy import policies_by_city, target_for
    from app.services.notify_sweep import _active_accounts, daily_target

    active = _active_accounts(session)
    target = daily_target(session)
    pmap = policies_by_city(session)
    return {
        "in_service_ids": [a.id for a in active],
        "in_service": len(active),
        "daily_target": target,
        "today_target": sum(target_for(session, a, pmap) for a in active),
    }


@router.get("/group-counts", dependencies=[Depends(require_admin)])
def group_counts(session: Session = Depends(get_session)):
    """每台设备读到了几个群。

    开自动推送之前必须能看到这个数：一个群都没有的号，开了也是每天静静地推 0 条，
    界面上和「推成功了」长得一模一样。逐台调 /devices/{id}/groups 要请求 37 次，
    所以一次给全。
    """
    rows = session.exec(
        select(ChatGroup.device_id, func.count()).group_by(ChatGroup.device_id)
    ).all()
    return {str(device_id): int(n) for device_id, n in rows}


@router.patch("/{device_id}/auto-broadcast", dependencies=[Depends(require_admin)])
def set_auto_broadcast(
    device_id: int,
    payload: DeviceAutoBroadcastUpdate,
    session: Session = Depends(get_session),
):
    """开/关某个号的自动群推送。和 auto-publish 对称，但**是两个独立开关** ——
    有的号适合往群里发东西、有的不适合，不能跟着发布走。"""
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    acc = get_account(session, device_id, payload.platform)
    acc.auto_broadcast = payload.auto_broadcast
    acc.updated_at = utcnow()
    session.add(acc)
    session.commit()
    session.refresh(device)
    return normalize_datetimes(serialize_device(session, device))


@router.post("/{device_id}/schedule", dependencies=[Depends(require_admin)])
def schedule_publish(
    device_id: int,
    payload: SchedulePostsRequest,
    session: Session = Depends(get_session),
):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    account = get_account(session, device_id, payload.platform)
    return schedule_posts(session, account, payload.times, payload.content_ids)


@router.post("/schedule-batch", dependencies=[Depends(require_admin)])
def schedule_batch(
    payload: BatchScheduleRequest,
    session: Session = Depends(get_session),
):
    """跨账号批量排期: apply the same time-slots to every selected account. Content is
    auto-assigned at random; because each consume locks its piece (→PUBLISHING), no
    two accounts get the same content. Skips a device that's missing or 异常, and
    stops filling once the shared content pool runs out."""
    results: list[dict] = []
    total_scheduled = 0
    for device_id in payload.device_ids:
        device = session.get(Device, device_id)
        if not device:
            results.append(
                {"device_id": device_id, "name": f"#{device_id}", "scheduled": 0,
                 "skipped_quota": 0, "pool_exhausted": False, "error": "设备不存在"}
            )
            continue
        account = get_account(session, device_id, payload.platform)
        if account.health != "normal":
            results.append(
                {"device_id": device_id, "name": device.name, "scheduled": 0,
                 "skipped_quota": 0, "pool_exhausted": False, "error": "账号状态异常，已跳过"}
            )
            continue
        r = schedule_posts(session, account, payload.times)
        total_scheduled += r["scheduled"]
        results.append(
            {"device_id": device_id, "name": device.name, "scheduled": r["scheduled"],
             "skipped_quota": r["skipped_quota"], "pool_exhausted": r["pool_exhausted"]}
        )
    pool_empty = any(r.get("pool_exhausted") for r in results)
    return {
        "total_scheduled": total_scheduled,
        "accounts": len(payload.device_ids),
        "pool_exhausted": pool_empty,
        "results": results,
    }


@router.patch("/{device_id}/health", dependencies=[Depends(require_admin)])
def set_health(
    device_id: int,
    payload: DeviceHealthUpdate,
    session: Session = Depends(get_session),
):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    acc = get_account(session, device_id, payload.platform)
    acc.health = payload.health
    acc.health_message = payload.health_message
    acc.updated_at = utcnow()
    session.add(acc)
    # ⚠ 必须同时清设备上那份副本。心跳（上面 heartbeat）**同时**写
    # `device.health` 和 `acc.health`，而这个「标记正常/异常」的按钮以前只写
    # 账号那一份 —— 于是设备那份一旦被置成 abnormal 就再也回不去：
    # 全站没有任何接口或界面能把它改回来。
    # 后果是告警铃铛里挂着 7 条**永远清不掉**的「设备异常」，而设备页显示全绿
    # （`serialize_device` 用账号的 health 覆盖了同名字段，设备那份根本看不见）。
    # 一个清不掉的红点比没有红点更糟：它把整个铃铛训练成可以无视的东西。
    if payload.platform == "douyin":
        device.health = payload.health
        device.health_message = payload.health_message
        device.updated_at = utcnow()
        session.add(device)
    session.commit()
    session.refresh(device)
    return normalize_datetimes(serialize_device(session, device))


@router.get("/{device_id}/groups", dependencies=[Depends(require_admin)])
def list_groups(device_id: int, session: Session = Depends(get_session)):
    groups = session.exec(
        select(ChatGroup)
        .where(ChatGroup.device_id == device_id)
        .order_by(ChatGroup.group_name)
    ).all()
    return [normalize_datetimes(g.model_dump()) for g in groups]


@router.delete("/{device_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_device(device_id: int, session: Session = Depends(get_session)):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    for acc in accounts_for(session, device_id):
        session.delete(acc)
    session.delete(device)
    session.commit()


@router.delete(
    "/{device_id}/accounts/{platform}",
    dependencies=[Depends(require_admin)],
)
def delete_account(
    device_id: int, platform: str, session: Session = Depends(get_session)
):
    """Delete ONE platform-account card (e.g. just the 小红书 account), leaving the
    device and its other platform accounts intact. Only if it was the device's LAST
    account is the device itself removed. Fixes the old bug where the card's delete
    button nuked the whole device."""
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    accts = accounts_for(session, device_id)
    target = next((a for a in accts if a.platform == platform), None)
    if not target:
        raise HTTPException(status_code=404, detail="该设备没有此平台账号")
    session.delete(target)
    device_removed = False
    if not [a for a in accts if a.platform != platform]:
        session.delete(device)
        device_removed = True
    session.commit()
    return {"status": "ok", "device_removed": device_removed}


@router.get("", dependencies=[Depends(require_admin)])
def list_devices(session: Session = Depends(get_session)):
    devices = session.exec(select(Device).order_by(Device.created_at.desc())).all()
    now = utcnow()
    result = []
    for device in devices:
        heartbeat = device.last_heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        stale = (now - heartbeat).total_seconds() > get_settings().device_offline_seconds
        item = serialize_device(session, device)
        # A device that stopped sending heartbeats is offline even if it was
        # mid-task — otherwise a crashed "busy" device shows online forever.
        if stale:
            item["status"] = "offline"
        result.append(item)
    return normalize_datetimes(result)
