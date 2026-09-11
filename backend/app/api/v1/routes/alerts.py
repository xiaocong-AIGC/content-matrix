"""运维告警中心 — 主动暴露当前矩阵的异常状态，给多机运营做预警。

与通知中心 (notifications) 不同：通知是"发布结果流水"，告警是"当前还没解决的问题"。

七类：设备掉线 / 无障碍未绑定 / 设备异常 / 账号未登录 / 账号异常 /
近 24h 发布失败 / **任务卡住** / **排队积压**。

后两类是补的一个盲区：之前所有告警都在说"某件事失败了"，没有一类在说
"什么都没发生"。而这套系统最可能的故障恰恰是后者 —— 编排器没起、清扫循环挂了、
手机上的 App 被系统清掉：设备照样在线、没有任何失败任务，控制台**全绿**，
只是一整天一条都没发出去。

纯派生只读（无新表），前端轮询展示。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.enums import TaskStatus
from app.core.serialization import normalize_datetimes
from app.db.session import get_session
from app.models.entities import Device, DeviceAccount, PublishTask, utcnow
from app.services.city_policy import policies_by_city, publishes

router = APIRouter(prefix="/alerts", tags=["alerts"])

# 失败任务只看近 24h，避免历史失败长期刷屏。
_FAILED_WINDOW = timedelta(hours=24)
# 一次最多列这么多条失败。超出的不再逐条列，只汇总成一条 —— 铃铛里挂着两百条
# 一模一样的失败，等于什么都没说。
_FAILED_CAP = 50
# waiting_confirmation 停留多久算"卡住"。后台每分钟扫一次、超过
# confirmation_timeout_seconds(默认 300s) 就自动判失败，所以还能停在这儿超过
# 10 分钟，说明**那个清扫循环本身没在跑** —— 这是"引擎停了"最早能看见的信号。
_STUCK_CONFIRM = timedelta(minutes=10)
# 卡在 running/leased 里不动多久算异常。后台 `fail_stalled_tasks` 10 分钟就该
# 收掉它，所以超过 15 分钟还挂着，同样说明清扫没在跑。
# 2026-09-09 实测：一条任务在 running 里卡了 32 分钟无人过问 —— 当时这一类
# 根本没有告警，它是被无障碍熔断顺手带走的，报的原因还是错的。
_STUCK_RUNNING = timedelta(minutes=15)
# 到点还没被领走多久算"积压"。设备在线还领不走 = 领取链路有问题
# （编排器没起、Agent 没在轮询、手机被一条僵尸任务占着）。
_BACKLOG_AFTER = timedelta(minutes=30)
# 无障碍掉多久才算"卡住"、值得叫人。
# **不是拍脑袋**：2026-09-08 实测当天 7 台掉过无障碍，6 台几分钟内自己回来了，
# 只有 1 台是真卡住。编排器每 ~5 分钟自愈一次，15 分钟 = 它已经试过三轮还没成。
# 低于这个数就报，等于每天几十条「抖了一下」的噪音，铃铛很快就没人看了。
_A11Y_STUCK = timedelta(minutes=15)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


@router.get("")
def list_alerts(session: Session = Depends(get_session)):
    """当前活跃告警，按严重度(critical>warning>info)排序。"""
    pmap = policies_by_city(session)
    now = utcnow()
    offline_after = get_settings().device_offline_seconds
    devices = session.exec(select(Device)).all()
    dev_by_id = {d.id: d for d in devices}
    all_accounts = session.exec(select(DeviceAccount)).all()
    accounts_by_device: dict[int, list] = {}
    for a in all_accounts:
        accounts_by_device.setdefault(a.device_id, []).append(a)
    alerts: list[dict] = []

    # 1) 设备掉线：心跳超时（即便它上次状态是 online / busy）。
    for d in devices:
        hb = _aware(d.last_heartbeat_at)
        stale_secs = (now - hb).total_seconds() if hb else 1e9
        if stale_secs > offline_after:
            mins = int(stale_secs // 60)
            alerts.append(
                {
                    "id": f"device-offline-{d.id}",
                    "severity": "critical",
                    "kind": "device_offline",
                    "title": f"设备掉线：{d.name}",
                    "detail": f"已 {mins} 分钟无心跳（阈值 {offline_after}s），请检查 USB/Agent 进程。",
                    "target": d.name,
                    "device_id": d.id,
                    "at": d.last_heartbeat_at,
                }
            )
        # 无障碍掉了：这台手机做不了任何任务。**但要分清抖动和卡死** ——
        # 掉几分钟又自己回来是常态（当天 7 台里 6 台如此），而发布失败本来就会
        # 自动重排到今天后面的时段，所以那种情况不需要惊动任何人。
        # 只有连自愈都救不回来、而且持续超过 15 分钟的，才值得叫人。
        since = _aware(d.accessibility_since)
        if stale_secs <= offline_after and d.accessibility_ok is False:
            down = (now - since) if since else None
            if down is None or down >= _A11Y_STUCK:
                mins = int(down.total_seconds() // 60) if down else None
                accs = accounts_by_device.get(d.id) or []
                # 按「实际发不发」判断，不是老开关 —— 城市开着时老开关可能是 false
                busy = [a for a in accs if publishes(session, a, pmap) or a.auto_broadcast]
                howlong = f"已经 {mins} 分钟" if mins is not None else "有一段时间"
                if busy:
                    who = "、".join(a.nickname or f"账号#{a.id}" for a in busy[:3])
                    detail = (
                        f"{howlong}接不了任务了，自动重连没成功。"
                        f"{who} 开着自动发布或自动推送，今天的篇数会少。"
                        "重启这台手机可以恢复。"
                    )
                else:
                    detail = (
                        f"{howlong}接不了任务了，自动重连没成功。"
                        "这台手机上的号都没开自动化，暂时不影响出稿——"
                        "下次要用它之前重启一次就行。"
                    )
                alerts.append(
                    {
                        "id": f"device-a11y-{d.id}",
                        # 没开自动化的先不算 critical：它今天不产生任何损失，
                        # 和「正在少发」的那种混在一起会让真问题被淹掉。
                        "severity": "critical" if busy else "warning",
                        "kind": "a11y_unbound",
                        "title": f"{d.name} 做不了任务",
                        "detail": detail,
                        "target": d.name,
                        "device_id": d.id,
                        "at": d.accessibility_since or d.updated_at,
                    }
                )
        # 设备级健康异常。**只报没有账号的设备** —— 有账号的走下面 2) 的
        # account_health，那才是真相：「被封禁」是账号的状态，不是这台手机的。
        # `Device.health` 是心跳留下的一份影子副本，语义上和账号那份重复，
        # 而且历史上只进不出（见 devices.py set_health 的注释）。
        # 两份都报的结果是同一件事在铃铛里出现两次，且其中一次清不掉。
        if d.health and d.health != "normal" and not accounts_by_device.get(d.id):
            alerts.append(
                {
                    "id": f"device-health-{d.id}",
                    "severity": "warning",
                    "kind": "device_health",
                    "title": f"设备异常：{d.name}",
                    "detail": d.health_message or d.health,
                    "target": d.name,
                    "device_id": d.id,
                    "at": d.updated_at,
                }
            )

    # 2) 账号异常：未登录 / 限流 / 违规 / 封禁。
    for a in all_accounts:
        if a.logged_in is False:
            dev = dev_by_id.get(a.device_id)
            name = a.nickname or (dev.name if dev else f"设备#{a.device_id}")
            alerts.append(
                {
                    "id": f"account-logout-{a.id}",
                    "severity": "warning",
                    "kind": "account_logout",
                    "title": f"账号未登录：{name}（{a.platform}）",
                    "detail": "该设备未登录账号，无法发布——请在手机上登录后点「更新数据」刷新。",
                    "target": name,
                    "device_id": a.device_id,
                    "at": a.updated_at,
                }
            )
        if a.health and a.health != "normal":
            dev = dev_by_id.get(a.device_id)
            name = a.nickname or (dev.name if dev else f"设备#{a.device_id}")
            alerts.append(
                {
                    "id": f"account-health-{a.id}",
                    "severity": "warning",
                    "kind": "account_health",
                    "title": f"账号异常：{name}（{a.platform}）",
                    "detail": a.health_message or a.health,
                    "target": name,
                    "device_id": a.device_id,
                    "at": a.updated_at,
                }
            )

    # 3) 近 24h 发布失败。
    # ⚠ 时间窗必须写进 SQL：以前是"取最新 50 条再在 Python 里滤掉 24h 之外的"，
    # 一旦某天失败超过 50 条，多出来的就被**静默丢掉**，铃铛上的数字会比实际少，
    # 而少的那部分恰恰是最该看的那天。现在先按窗口过滤，再封顶，超额单独汇总。
    since = now - _FAILED_WINDOW
    failed = session.exec(
        select(PublishTask)
        .where(PublishTask.status == TaskStatus.FAILED)
        .where(PublishTask.finished_at.is_not(None))
        .where(PublishTask.finished_at >= since.replace(tzinfo=None))
        .order_by(PublishTask.finished_at.desc())
        .limit(_FAILED_CAP + 1)
    ).all()
    overflow = max(len(failed) - _FAILED_CAP, 0)
    for t in failed[:_FAILED_CAP]:
        dev = dev_by_id.get(t.device_id)
        alerts.append(
            {
                "id": f"task-failed-{t.id}",
                "severity": "warning",
                "kind": "task_failed",
                "title": f"发布失败：{t.publish_title or t.cover_title or t.name}",
                "detail": (t.error_message or "未知原因")
                + (f" · {dev.name}" if dev else "")
                + f"（{t.platform}）",
                "target": dev.name if dev else None,
                "device_id": t.device_id,
                "at": t.finished_at,
            }
        )

    if overflow:
        alerts.append(
            {
                "id": "task-failed-overflow",
                "severity": "critical",
                "kind": "task_failed_many",
                "title": f"近 24 小时失败超过 {_FAILED_CAP} 条",
                "detail": f"上面只列了最近 {_FAILED_CAP} 条，还有更多没列出来。"
                "这个量级不该逐条看，请到执行记录里按设备筛一遍找共同原因。",
                "target": None,
                "device_id": None,
                "at": failed[0].finished_at if failed else None,
            }
        )

    # 4) 卡住的任务：转人工之后没人管，或者卡在 running 里不动 ——
    #    两种都意味着自动判死没有发生，也就是后台清扫本身可能停了。
    stuck = [
        t
        for t in session.exec(
            select(PublishTask).where(
                PublishTask.status == TaskStatus.WAITING_CONFIRMATION
            )
        ).all()
        if _aware(t.waiting_since or t.updated_at)
        and _aware(t.waiting_since or t.updated_at) < now - _STUCK_CONFIRM
    ]
    # running/leased 里不动的：判据用 started_at，因为 updated_at 会被续租约刷新
    stuck += [
        t
        for t in session.exec(
            select(PublishTask).where(
                PublishTask.status.in_([TaskStatus.RUNNING, TaskStatus.LEASED])
            )
        ).all()
        if _aware(t.started_at) and _aware(t.started_at) < now - _STUCK_RUNNING
    ]
    if stuck:
        names = [
            (dev_by_id.get(t.device_id).name if dev_by_id.get(t.device_id) else "未知设备")
            for t in stuck
        ]
        mins = int(
            (now - min(_aware(t.waiting_since or t.started_at or t.updated_at)
                       for t in stuck)).total_seconds() // 60
        )
        alerts.append(
            {
                "id": "task-stuck-confirm",
                "severity": "critical",
                "kind": "task_stuck",
                "title": f"{len(stuck)} 条任务卡住不动，最久 {mins} 分钟",
                "detail": "正常情况下卡住超过 10 分钟就会被自动结束、把手机让出来。"
                "现在没有结束，说明后台的清扫没在跑 —— 这几台手机不会再接新任务，"
                "请重启后端服务。涉及：" + "、".join(sorted(set(names))[:6]),
                "target": names[0] if names else None,
                "device_id": stuck[0].device_id,
                "at": stuck[0].waiting_since or stuck[0].started_at or stuck[0].updated_at,
            }
        )

    # 5) 排队积压：到点了还没被领走，而那台手机是在线的。
    # 手机不在线的那些不在这里报 —— 上面 1) 已经报过掉线，同一件事报两次
    # 会让人以为是两个问题。
    backlog: dict[int, int] = {}
    oldest: dict[int, datetime] = {}
    for t in session.exec(
        select(PublishTask).where(PublishTask.status == TaskStatus.QUEUED)
    ).all():
        due = _aware(t.scheduled_at or t.created_at)
        if not due or due > now - _BACKLOG_AFTER:
            continue
        dev = dev_by_id.get(t.target_device_id)
        if dev is None:
            continue
        hb = _aware(dev.last_heartbeat_at)
        if not hb or (now - hb).total_seconds() > offline_after:
            continue  # 掉线的已经报过了
        backlog[dev.id] = backlog.get(dev.id, 0) + 1
        if dev.id not in oldest or due < oldest[dev.id]:
            oldest[dev.id] = due
    for dev_id, count in backlog.items():
        dev = dev_by_id[dev_id]
        waited = int((now - oldest[dev_id]).total_seconds() // 60)
        alerts.append(
            {
                "id": f"queue-backlog-{dev_id}",
                "severity": "critical",
                "kind": "queue_backlog",
                "title": f"{dev.name} 有 {count} 条到点没发",
                "detail": f"最早一条已经等了 {waited} 分钟，而这台手机是在线的。"
                "说明它没在领任务：可能编排器没起来，也可能手机上的 App 被系统清掉了。"
                "请检查这台设备上的 Agent 是否还在运行。",
                "target": dev.name,
                "device_id": dev_id,
                "at": oldest[dev_id],
            }
        )

    rank = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda x: (rank.get(x["severity"], 9), str(x["at"] or "")), reverse=False)
    counts = {"critical": 0, "warning": 0, "info": 0}
    for x in alerts:
        counts[x["severity"]] = counts.get(x["severity"], 0) + 1
    return normalize_datetimes({"alerts": alerts, "counts": counts, "total": len(alerts)})
