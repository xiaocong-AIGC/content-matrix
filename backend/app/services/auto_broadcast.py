"""群推送自动化：每天 N 次，时刻落在设定的时间段内随机。

用户要的形态和自动发布一样：「每天自动推送次数可以设置，然后时间可以在规定的时间内
随机……发布也是」。时间那套在 `schedule_windows` 里，发布和群发共用。

消息从**群推送消息库**（`BroadcastMessage`）里挑，可以反复用 —— 这是它和作品池
最大的区别。两种挑法（`broadcast:mode`）：

* `same`（默认）：当天所有号发**同一条**，每天换一条。适合统一口径的通知。
* `rotate`：每个号各挑各的。适合不想让不同群看到一模一样的话。

排程幂等靠两件事：① 时刻是**可复现随机**（同一个号同一天算多少遍都一样，
见 schedule_windows）；② 建任务前按 `(设备, 时刻)` 查一次重。所以 5 分钟一轮的
loop 反复跑、进程重启，都不会重复排。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from app.core.enums import TaskStatus
from app.models.entities import (
    BroadcastMessage,
    ChatGroup,
    Device,
    DeviceAccount,
    ImageAsset,
    PublishTask,
    utcnow,
)
from app.services import settings_store
from app.services.schedule_windows import (
    CN_TZ,
    describe,
    due_times,
    parse_windows,
    plan_times,
    to_utc_naive,
)

COUNT_KEY = "broadcast:daily_count"      # 每天推几次，0 = 关掉
WINDOWS_KEY = "broadcast:windows"        # "10:00-11:00,14:00-15:00"
MODE_KEY = "broadcast:mode"              # same | rotate

DEFAULT_COUNT = 0                        # 默认关着 —— 这是往真实群里发东西，得有人明确打开
DEFAULT_WINDOWS = "10:00-11:00,14:00-15:00"
DEFAULT_MODE = "same"

# 排程只补最近这么久内该发的点。再往前的就算了：早上 10 点该发的，
# 下午 4 点才补上去发已经没意义，还容易在同一时刻堆一串。
CATCH_UP = timedelta(hours=2)

# 到点了却一直没被领走（设备忙/离线）的自动群推送，超过这个时间就作废。
# 作品发晚了还能接受，**群消息发晚了是打扰** —— 早上 10 点该推的东西
# 半夜才发进群里，比不推更糟。
STALE_AFTER = timedelta(hours=6)
# 自动排的群推送用这个前缀起名，手动发的是「群发：」。作废只针对自动排的，
# 免得把运营自己定时的那条给撤了。
AUTO_NAME_PREFIX = "群推送："
# 领任务是 order by priority desc, created_at，默认都是 50。群推送压到 50 以下：
# 一台手机同时排着一条发布和一条群推送时，**先把作品发出去** —— 作品是产能，
# 群推送晚几分钟没关系，反过来就不行。
BROADCAST_PRIORITY = 30


def daily_count(session: Session) -> int:
    raw = settings_store.get(session, COUNT_KEY, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return max(value, 0)


def windows(session: Session) -> list[tuple[int, int]]:
    raw = settings_store.get(session, WINDOWS_KEY, "") or DEFAULT_WINDOWS
    return parse_windows(raw)


def mode(session: Session) -> str:
    value = (settings_store.get(session, MODE_KEY, "") or DEFAULT_MODE).strip()
    return value if value in ("same", "rotate") else DEFAULT_MODE


def _usable(rows: list[BroadcastMessage], city: str) -> list[BroadcastMessage]:
    """从一批已启用的消息里挑这个城市能用的：本城的 + 通用的。用得少的排前面。

    和 `_usable_messages` 是同一套规则，只是**不自己查库** —— 预览要为几十个号
    各算一遍，每个号跑一次全表扫就是上百次查询。
    """
    usable = [
        m for m in rows
        if m.text.strip() and (not m.city or m.city in ("通用", "全国") or m.city == city)
    ]
    usable.sort(key=lambda m: (0 if m.city == city and city else 1, m.sent_count, m.id or 0))
    return usable


def _usable_messages(session: Session, city: str) -> list[BroadcastMessage]:
    """这个城市的号能用的消息：本城的 + 通用的。用得少的排前面。"""
    rows = session.exec(
        select(BroadcastMessage).where(BroadcastMessage.enabled == True)  # noqa: E712
    ).all()
    usable = [
        m for m in rows
        if m.text.strip() and (not m.city or m.city in ("通用", "全国") or m.city == city)
    ]
    # 本城的优先于通用的；同一档里发得少的优先
    usable.sort(key=lambda m: (0 if m.city == city and city else 1, m.sent_count, m.id or 0))
    return usable


def _pick(
    messages: list[BroadcastMessage], seed: str, *, balance: bool
) -> BroadcastMessage | None:
    """从候选里挑一条，挑法是确定性的（同样的种子永远挑到同一条）。

    ⚠ `balance` 必须跟着模式走。`same` 模式下**不能**按"谁发得少挑谁"：每排一个号
    sent_count 就 +1，第二个号看到的"发得最少"已经变了，于是同一天的号挑到了不同的
    消息 —— 而 same 模式的全部意义就是所有号发同一条。所以 same 只按种子在**固定
    顺序**里挑（顺序由 id 决定，不随发送次数漂移），换条靠日期变。
    """
    if not messages:
        return None
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    if balance:
        # rotate 模式：只在"用得最少"的那一档里挑，避免有的消息一直不被翻到
        fewest = min(m.sent_count for m in messages)
        pool = [m for m in messages if m.sent_count == fewest] or messages
    else:
        pool = sorted(messages, key=lambda m: m.id or 0)
    return pool[int(digest, 16) % len(pool)]


def _seed_for(moment: datetime, index: int, account_id: int | None, same_for_all: bool) -> str:
    """挑消息用的种子。

    ⚠ **只能有这一份**。排程和预览各写一遍的话，哪天改了其中一个，预览就开始骗人 ——
    而且骗得毫无征兆：界面上说 10 点推 A，群里冒出来的是 B。

    same = 当天所有号发同一条（种子只含日期和第几次）；
    rotate = 各挑各的（种子里加上账号）。
    """
    if same_for_all:
        return f"{moment:%Y%m%d}|{index}"
    return f"{moment:%Y%m%d}|{index}|{account_id}"


def _warn_no_groups(session: Session, account: DeviceAccount) -> None:
    """开了自动推送、却一个群都没有 —— 每天提醒一次。

    群列表是 Agent 启动时扫一次的（而且是先删后插），所以"群没了"是个真实状态，
    不是不可能发生的边界。
    """
    from app.services import notify

    device = session.get(Device, account.device_id) if account.device_id else None
    notify.enqueue(
        event="broadcast_no_groups",
        dedup_key=f"nogroups:{account.id}:{datetime.now(CN_TZ):%Y%m%d}",
        title=f"⚠️ {account.nickname} 开着自动推送，但没有可推的群",
        severity="warning",
        city=notify.real_city(account.city),
        device_id=account.device_id,
        account_id=account.id,
        payload={"lines": [
            f"{device.name if device else '这台手机'} 上一个群都没读到，"
            "所以今天一条都不会推",
            "去设备页刷新一下这个号的群列表，或者确认它还在群里",
        ]},
    )


def expire_stale(session: Session, now: datetime | None = None) -> int:
    """把过点太久还没被领走的自动群推送作废。返回作废了几条。

    领任务只看 `scheduled_at <= now`，没有过期概念：设备忙或离线时，早上 10 点该推的
    可能凌晨 3 点才被领走。作品发晚了还能接受，群消息发晚了是打扰。
    只动自动排的（按名字前缀认），不碰运营自己定时的那条。
    """
    now = now or datetime.now(timezone.utc)
    cutoff = to_utc_naive((now - STALE_AFTER).astimezone(CN_TZ))
    stale = session.exec(
        select(PublishTask)
        .where(PublishTask.status == TaskStatus.QUEUED)
        .where(PublishTask.publish_type == "group_message")
        .where(PublishTask.scheduled_at.is_not(None))
        .where(PublishTask.scheduled_at < cutoff)
        .where(PublishTask.name.like(f"{AUTO_NAME_PREFIX}%"))
    ).all()
    for task in stale:
        task.status = TaskStatus.CANCELLED
        task.current_step = "cancelled"
        task.error_message = "过了该推的时间太久，这条就不推了"
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        session.add(task)
    if stale:
        session.commit()
    return len(stale)


def plan_for_account(
    session: Session, account: DeviceAccount, now: datetime | None = None
) -> int:
    """给一个号把今天该排的群推送补上。返回新建了几条。"""
    if not account.auto_broadcast or account.health != "normal":
        return 0
    count = daily_count(session)
    slots = windows(session)
    if count <= 0 or not slots:
        return 0

    now = now or datetime.now(timezone.utc)
    due = due_times(
        windows=slots, count=count, now=now, key=f"broadcast:{account.id}"
    )
    if not due:
        return 0

    groups = session.exec(
        select(ChatGroup).where(ChatGroup.device_id == account.device_id)
    ).all()
    names = [g.group_name for g in groups if g.group_name]
    if not names:
        # 静默跳过是这里最糟的选择：这个号会每天安安静静地推 0 条，
        # 而界面上和"推成功了"长得一模一样。
        _warn_no_groups(session, account)
        return 0

    city = (account.city or "").strip()
    created = 0
    cn_now = now.astimezone(CN_TZ)
    for index, moment in enumerate(due):
        if cn_now - moment > CATCH_UP:
            continue                      # 错过太久就跳过这一次，不补
        when = to_utc_naive(moment)
        exists = session.exec(
            select(PublishTask)
            .where(PublishTask.target_device_id == account.device_id)
            .where(PublishTask.publish_type == "group_message")
            .where(PublishTask.scheduled_at == when)
        ).first()
        if exists:
            continue                      # 已经排过这个点了（loop 反复跑的正常情况）

        same_for_all = mode(session) == "same"
        message = _pick(
            _usable_messages(session, city),
            _seed_for(moment, index, account.id, same_for_all),
            balance=not same_for_all,
        )
        if message is None:
            break                         # 消息库空了，今天不排了

        session.add(PublishTask(
            name=f"{AUTO_NAME_PREFIX}{message.text[:16]}",
            publish_type="group_message",
            body=message.text,
            publish_mode="auto_publish",
            target_device_id=account.device_id,
            image_path=message.image_path,
            mention_all=message.mention_all,
            target_groups_json=json.dumps(names, ensure_ascii=False),
            scheduled_at=when,
            status=TaskStatus.QUEUED,
            priority=BROADCAST_PRIORITY,
        ))
        message.sent_count += 1
        message.last_sent_at = utcnow()
        session.add(message)
        created += 1

    if created:
        session.commit()
    return created


def stop_now(session: Session) -> int:
    """立刻停：把每天次数置 0，**并且取消今天还没执行的自动群推送**。

    只置 0 是不够的 —— 那只挡住未来的排程，今天已经进队列的照发。运营点了"关闭"、
    群里还在冒消息，是这个功能最危险的一个洞。返回取消了几条。

    只动自动排的（按名字前缀认），不碰运营自己定时的那条。
    """
    settings_store.put(session, COUNT_KEY, "0")
    pending = session.exec(
        select(PublishTask)
        .where(PublishTask.status == TaskStatus.QUEUED)
        .where(PublishTask.publish_type == "group_message")
        .where(PublishTask.name.like(f"{AUTO_NAME_PREFIX}%"))
    ).all()
    for task in pending:
        task.status = TaskStatus.CANCELLED
        task.current_step = "cancelled"
        task.error_message = "运营点了停止，这条不推了"
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        session.add(task)
    session.commit()
    return len(pending)


def pending_today(session: Session) -> int:
    """今天还排着、还没执行的自动群推送有几条 —— 关闭按钮上要显示这个数。"""
    return session.exec(
        select(func.count())
        .select_from(PublishTask)
        .where(PublishTask.status == TaskStatus.QUEUED)
        .where(PublishTask.publish_type == "group_message")
        .where(PublishTask.name.like(f"{AUTO_NAME_PREFIX}%"))
    ).one()


def plan_all(session: Session, now: datetime | None = None) -> int:
    """给所有开了群推送的号排今天的量。由后台 loop 调，必须幂等。"""
    total = 0
    try:
        expire_stale(session, now)
    except Exception:  # noqa: BLE001 — 清理失败不该挡住排程
        session.rollback()
    accounts = session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.platform == "douyin")
        .where(DeviceAccount.auto_broadcast == True)  # noqa: E712
    ).all()
    for account in accounts:
        try:
            total += plan_for_account(session, account, now)
        except Exception:  # noqa: BLE001 — 一个号排不了不该拖垮其它号
            session.rollback()
    return total


# 预览里每一格的状态。用**后端**算，不能让前端拿 HH:MM 和 new Date() 比 ——
# 运营的机器时区不保证是 +8，比出来的结果会差 8 小时。
STATE_FROM_TASK = {
    TaskStatus.SUCCEEDED: "sent",
    TaskStatus.FAILED: "failed",
    TaskStatus.CANCELLED: "cancelled",
}


def _blocked_reason(
    account: DeviceAccount, groups: int, usable: int
) -> str | None:
    """这个号今天一条都不会推的原因。没有原因就返回 None。

    这三种情况在界面上必须说出来，因为它们**长得和"推成功了"一模一样**：
    每天安安静静地推 0 条，没有失败任务、没有告警。
    """
    if account.health != "normal":
        return "账号状态异常，排程会跳过这个号"
    if not groups:
        return "这台手机没读到群，推不出去"
    if not usable:
        return "没有可用的消息"
    return None


def preview_today(
    session: Session,
    now: datetime | None = None,
    *,
    override: dict | None = None,
) -> list[dict]:
    """今天每个开了自动推送的号，几点推、推的是哪一条、现在是什么状态。

    三条必须守住的准确性（每一条都对应一个"页面说会推、实际不推"的骗人场景）：

    **① 排程会跳过的号，预览也要跳过 / 说明白。** `plan_for_account` 除了
    auto_broadcast 还要求 `health == "normal"`。一个异常账号在预览里有时刻、
    有正文，实际一条都不推 —— 这直接打破这一页唯一的价值。

    **② 过了补发窗口的时刻不会再推。** 排程对 `cn_now - moment > CATCH_UP` 直接
    跳过（发晚了的群消息是打扰）。预览必须把这些标成"错过了"，而不是"待推"。

    **③ 改了时间段之后，已经排进队列的旧任务照发。** 它们的时刻不在新计划里，
    按时刻索引会被直接丢掉 —— 中午改一次时间段，上午排好还没发的消息就从页面上
    蒸发了，然后照发。这些单独作为"计划外"的行发出来。

    另外：**已排的读真实任务**（那是事实），未排的按和排程完全相同的种子推算。
    rotate 模式下 `_pick(balance=True)` 依赖 sent_count，而 sent_count 随排程往上走，
    所以未来时刻的推算只是"预计"—— 每格用 `certain` 标出来，文案照着写。
    """
    now = now or datetime.now(timezone.utc)
    # 试算：拿一套还没保存的设置算一遍。**只换设置，不换算法** ——
    # 时刻和挑消息仍然走 plan_times / _seed_for，所以试算出来的就是保存后会发生的。
    if override:
        count = int(override.get("daily_count", 0) or 0)
        slots = parse_windows(str(override.get("windows") or ""))
        same_for_all = (override.get("mode") or "same") == "same"
    else:
        count = daily_count(session)
        slots = windows(session)
        same_for_all = mode(session) == "same"
    cn_now = now.astimezone(CN_TZ)
    cn_today = cn_now.date()

    accounts = session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.platform == "douyin")
        .where(DeviceAccount.auto_broadcast == True)  # noqa: E712
    ).all()
    if not accounts:
        return []

    # ⚠ 一次查完再分组。这个接口会被页面反复调，而 App 的全局刷新是 2 秒一轮 ——
    # 每个账号跑一次全表扫的话，37 个号就是上百次查询。这套系统已经吃过
    # 「每日回采阻塞心跳、20 台同时被判掉线」的亏。
    device_ids = {a.device_id for a in accounts if a.device_id}
    group_counts: dict[int, int] = {
        d: n
        for d, n in session.exec(
            select(ChatGroup.device_id, func.count())
            .where(ChatGroup.device_id.in_(device_ids or {0}))
            .group_by(ChatGroup.device_id)
        ).all()
    }
    devices = {
        d.id: d
        for d in session.exec(select(Device).where(Device.id.in_(device_ids or {0}))).all()
    }
    all_messages = session.exec(
        select(BroadcastMessage).where(BroadcastMessage.enabled == True)  # noqa: E712
    ).all()
    # 图片地址：库里存的是服务器文件路径，前端要的是能直接放进 <img> 的地址
    image_ids = {
        a.path: a.id
        for a in session.exec(select(ImageAsset)).all()
    }

    tasks_by_device: dict[int, list[PublishTask]] = {}
    for task in [] if override else session.exec(
        select(PublishTask)
        .where(PublishTask.target_device_id.in_(device_ids or {0}))
        .where(PublishTask.publish_type == "group_message")
        .where(PublishTask.name.like(f"{AUTO_NAME_PREFIX}%"))
        .where(PublishTask.scheduled_at.is_not(None))
    ).all():
        when = task.scheduled_at.replace(tzinfo=timezone.utc).astimezone(CN_TZ)
        if when.date() == cn_today:
            tasks_by_device.setdefault(task.target_device_id, []).append(task)

    by_text = {m.text: m for m in all_messages}

    def _image_url(path: str | None) -> str | None:
        asset_id = image_ids.get(path or "")
        return f"/api/v1/images/{asset_id}/file" if asset_id else None

    def _slot(
        *, time: str, state: str, orphan: bool, certain: bool,
        text: str, image_path: str | None, mention_all: bool,
        message_id: int | None, message_city: str | None,
        task_id: int | None, error: str | None,
    ) -> dict:
        """**两支（已排 / 待排）必须给出同一组字段**，否则前端要写两套判断。"""
        return {
            "time": time, "state": state, "orphan": orphan, "certain": certain,
            "text": text,
            "has_image": bool(image_path),
            "image_url": _image_url(image_path),
            "mention_all": mention_all,
            "message_id": message_id,
            "message_city": message_city,
            "task_id": task_id,
            "error": error,
        }

    def _slot_from_task(label: str, task: PublishTask, orphan: bool) -> dict:
        # 任务上没存 message_id（正文是拷过去的），按正文回查一次，
        # 让运营能从这一行点回消息库。查不到就是那条消息后来被删了。
        source = by_text.get(task.body or "")
        return _slot(
            time=label,
            state=STATE_FROM_TASK.get(task.status, "queued"),
            orphan=orphan, certain=True,
            text=task.body or "",
            image_path=task.image_path,
            mention_all=bool(task.mention_all),
            message_id=source.id if source else None,
            message_city=source.city if source else None,
            task_id=task.id,
            error=task.error_message or None,
        )

    out: list[dict] = []
    for account in accounts:
        if not account.nickname:
            continue
        groups = group_counts.get(account.device_id, 0)
        city = (account.city or "").strip()
        usable = _usable(all_messages, city)
        device = devices.get(account.device_id)
        actual = {
            f"{t.scheduled_at.replace(tzinfo=timezone.utc).astimezone(CN_TZ):%H:%M}": t
            for t in tasks_by_device.get(account.device_id, [])
        }
        planned = (
            plan_times(windows=slots, count=count, day=now, key=f"broadcast:{account.id}")
            if (count > 0 and slots)
            else []
        )

        entries = []
        for index, moment in enumerate(planned):
            label = f"{moment:%H:%M}"
            task = actual.pop(label, None)
            if task is not None:
                entries.append(_slot_from_task(label, task, orphan=False))
                continue
            message = _pick(
                usable, _seed_for(moment, index, account.id, same_for_all),
                balance=not same_for_all,
            )
            if moment > cn_now:
                state = "upcoming"
            elif cn_now - moment > CATCH_UP:
                state = "missed"       # 过了补发窗口，排程不会再补，这条今天不推了
            else:
                state = "due"          # 到点了，两分钟一轮的排程马上会把它排出去
            entries.append(_slot(
                time=label, state=state, orphan=False,
                # same 模式下挑法只看日期，说死没问题；rotate 依赖 sent_count，
                # 而 sent_count 会随排程漂移，所以只能算"预计"
                certain=same_for_all,
                text=message.text if message else "",
                image_path=message.image_path if message else None,
                mention_all=bool(message and message.mention_all),
                message_id=message.id if message else None,
                message_city=message.city if message else None,
                task_id=None, error=None,
            ))

        # 剩下的就是"计划外"的：时间段改过之后，旧任务还在队列里、照发
        for label, task in sorted(actual.items()):
            entries.append(_slot_from_task(label, task, orphan=True))
        entries.sort(key=lambda e: e["time"])

        out.append({
            "account": account.nickname,
            "account_id": account.id,
            "device_id": account.device_id,
            "device": device.name if device else None,
            "city": city or None,
            "health": account.health,
            "logged_in": account.logged_in,
            "blocked": _blocked_reason(account, groups, len(usable)),
            "groups": groups,
            "usable_messages": len(usable),
            "times": entries,
        })
    out.sort(key=lambda r: (r["city"] or "", r["account"]))
    return out


def upcoming_today(session: Session, now: datetime | None = None) -> int:
    """今天**还要推**几条。

    ⚠ 不是 `pending_today`。那个数的是队列里还没被手机领走的，而任务只在时刻
    到点之后才被创建 —— 所以它正常永远是 0~3，拿它当"今天还剩几条"会让运营
    看到一个恒等于 0 的数字。
    """
    return sum(
        1
        for row in preview_today(session, now)
        if not row["blocked"]
        for slot in row["times"]
        if slot["state"] in ("upcoming", "due")
    )


def preview_envelope(
    session: Session,
    now: datetime | None = None,
    *,
    override: dict | None = None,
) -> dict:
    """预览 + 一份算好的汇总。

    没有封套的话，页面顶上那几个数要前端遍历几十行自己算 —— 而且"空数组"分不清
    是「没打开」还是「打开了但一个账号都没开开关」，这两种情况给运营的下一步动作
    完全不同。
    """
    rows = preview_today(session, now, override=override)
    summary = {
        "accounts": len(rows),
        "blocked_accounts": sum(1 for r in rows if r["blocked"]),
        "planned": 0,
        "upcoming": 0, "due": 0, "queued": 0,
        "sent": 0, "failed": 0, "cancelled": 0, "missed": 0,
    }
    for row in rows:
        for slot in row["times"]:
            summary["planned"] += 1
            if slot["state"] in summary:
                summary[slot["state"]] += 1
    if override:
        count = int(override.get("daily_count", 0) or 0)
        raw = str(override.get("windows") or "")
        picked = (override.get("mode") or "same")
    else:
        count = daily_count(session)
        raw = settings_store.get(session, WINDOWS_KEY, DEFAULT_WINDOWS)
        picked = mode(session)
    slots = parse_windows(raw)
    return {
        "dry_run": bool(override),
        "today": f"{(now or datetime.now(timezone.utc)).astimezone(CN_TZ):%Y-%m-%d}",
        "daily_count": count,
        "windows": raw,
        "windows_readable": describe(slots),
        "mode": picked,
        "rows": rows,
        "summary": summary,
    }


def in_flight(session: Session) -> int:
    """正在被手机执行、**停不下来**的自动群推送有几条。

    stop_now 只取消 QUEUED。界面写「全部停止」而 20 秒后群里又冒出一条，
    是这个功能最伤信任的失败 —— 所以这个数要一起说出来。
    """
    return session.exec(
        select(func.count())
        .select_from(PublishTask)
        .where(PublishTask.status.in_([TaskStatus.LEASED, TaskStatus.RUNNING]))
        .where(PublishTask.publish_type == "group_message")
        .where(PublishTask.name.like(f"{AUTO_NAME_PREFIX}%"))
    ).one()
