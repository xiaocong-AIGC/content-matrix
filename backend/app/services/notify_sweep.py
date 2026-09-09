"""周期扫描：把系统状态变成企微群里能看懂的消息。

设计前提（用户原话）：**「有异常我们才会去看桌面应用和浏览器，没有异常我们都不去看」**。
所以企微群不是"告警通道"，是他们的**主界面**——正常运转的节奏也必须让他们看见，
否则群里一片安静时，他们无法区分「一切正常」和「系统死了」。

但每天几十条发布 + 群发，一条一消息会把群刷爆。所以分三档：

* **即时**：要立刻动手的（转人工、限流、内容告急、连续失败）→ 走 notify.enqueue，带去重和重试
* **节奏摘要**：每 2 小时一条，说清这段时间发了什么、生成了什么 → **不入队**，
  由本模块直接从任务表汇总，避免为 74 条常规事件各写一行台账
* **日报**：每天 09:00 一条，昨日总览 + 今日库存水位

`_scan_*` 只负责"发现状态"，发送一律交给 notify.enqueue / notify 的派发 loop。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from app.core.enums import ContentStatus, TaskStatus
from app.models.entities import (
    ContentItem,
    Device,
    DeviceAccount,
    PostMetric,
    PublishTask,
    utcnow,
)
from app.services import notify, settings_store
from app.services.notify import real_city

_CN_TZ = timezone(timedelta(hours=8))

# 状态类告警的判定门槛：低于这些时长不报，避免抖动刷屏
OFFLINE_AFTER_SECONDS = 600      # 掉线超过 10 分钟才报（回采期间的短暂中断不算）
A11Y_UNBOUND_AFTER_SECONDS = 300  # 无障碍未就绪超过 5 分钟才报
ACTIVITY_DIGEST_HOURS = 2         # 节奏摘要间隔
# 多久没发过就不再提醒它。用户定的：超过这个时间没发的号，肯定是走别的方式在发，
# 不归这套系统管，提醒了也没人会去动它。
ACTIVE_WINDOW_HOURS = 48
DIGEST_HOUR_CN = 9                # 日报时刻（中国时间）

TERMINAL_STATES = (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED)

_LAST_ACTIVITY_KEY = "notify:last_activity_digest"
_LAST_DAILY_KEY = "notify:last_daily_digest"
# 周报按 ISO 周号防重发，不能用日期 —— sweep 是 5 分钟一轮的，用日期的话
# 周五当天会被反复触发。
_LAST_WEEKLY_KEY = "notify:last_weekly_digest"
WEEKLY_WEEKDAY = 4          # 周五（Monday=0）
WEEKLY_HOUR_CN = 9
WEEKLY_MINUTE_CN = 30       # 排在日报 09:00 之后

# 每个号每天**期望**发几篇。注意和 DeviceAccount.daily_quota 的区别：
# quota 是**上限**（最多能发几篇），这里是**目标**（打算发几篇）。默认 1。
# 算库存水位必须用目标而不是上限 —— 用上限会把水位算成实际的两倍紧张，
# 天天报假的「内容告急」。运营可以在前端改这个值。
DAILY_TARGET_KEY = "publish:daily_target"
DEFAULT_DAILY_TARGET = 1


def daily_target(session: Session) -> int:
    raw = settings_store.get(session, DAILY_TARGET_KEY, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_TARGET
    return value if value > 0 else DEFAULT_DAILY_TARGET


def _expected_for(account: DeviceAccount, target: int) -> int:
    """实际期望 = min(全局目标, 该号的上限)，这样单号调低上限依然生效。"""
    return min(target, account.daily_quota if account.daily_quota is not None else target)


def _cn_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(_CN_TZ)


def _account_of(session: Session, device_id: int, platform: str = "douyin"):
    return session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.device_id == device_id)
        .where(DeviceAccount.platform == platform)
    ).first()


# ---------------------------------------------------------------------------
# 状态类（即时，走队列）
# ---------------------------------------------------------------------------
def _scan_devices(session: Session, now: datetime) -> None:
    """手机连不上 / 自动操作权限被关 / 抖音退出登录 / 账号被限制。

    只报**有号在跑**的手机 —— 十几台还没登号的机器是"还没开始做"，不是故障，
    报了只会让群里天天挂着一堆红色噪音。
    """
    for device in session.exec(select(Device)).all():
        account = _account_of(session, device.id)
        if account is None or not account.nickname:
            continue  # 没登号的机器不算故障
        city = real_city(account.city)
        who = account.nickname or device.name
        where = f"（{city}）" if city else ""

        heartbeat = device.last_heartbeat_at
        if heartbeat and heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        silent_for = int((now - heartbeat).total_seconds()) if heartbeat else 0
        if silent_for >= OFFLINE_AFTER_SECONDS:
            # 按 30 分钟分桶：一直连不上就每半小时提醒一次，不是每轮都刷
            bucket = int(now.timestamp()) // 1800
            notify.enqueue(
                event="device_offline",
                dedup_key=f"offline:{device.id}:{bucket}",
                title=f"⚠️ 手机连不上了　{who}{where}",
                severity="warning",
                city=city,
                device_id=device.id,
                account_id=account.id,
                payload={"lines": [
                    f"{device.name} 已经 {silent_for // 60} 分钟联系不上，"
                    "这台手机上的号发不了内容",
                    "常见原因：没电、断网、被别的应用占着，去看一下这台手机",
                ]},
            )
            continue  # 都连不上了，别再报它别的毛病

        if account.logged_in is False:
            notify.enqueue(
                event="account_logout",
                dedup_key=f"logout:{account.id}:{_cn_now():%Y%m%d}",
                title=f"⚠️ 账号要重新登录　{who}{where}",
                severity="warning",
                city=city,
                device_id=device.id,
                account_id=account.id,
                payload={"lines": [
                    f"{device.name} 上的抖音退出登录了，登回来之前这个号发不了内容",
                    "去这台手机上把抖音登回来",
                ]},
            )

        if device.accessibility_ok is False:
            bucket = int(now.timestamp()) // 1800
            notify.enqueue(
                event="a11y_unbound",
                dedup_key=f"a11y:{device.id}:{bucket}",
                title=f"⚠️ 这台手机发不了内容　{who}{where}",
                severity="warning",
                city=city,
                device_id=device.id,
                account_id=account.id,
                payload={"lines": [
                    f"{device.name} 的自动操作权限被系统关掉了，发内容和群发都会失败",
                    "重启这台手机就能恢复",
                ]},
            )

        if account.health and account.health != "normal":
            notify.enqueue(
                event="account_health",
                dedup_key=f"acct:{account.id}:{account.health}:{_cn_now():%Y%m%d}",
                title=f"🚨 账号被限制了　{who}{where}",
                severity="critical",
                city=city,
                device_id=device.id,
                account_id=account.id,
                payload={"lines": [
                    account.health_message or f"抖音把这个号标记成了 {account.health}",
                    f"{device.name} 上的这个号先别再发，去手机上看一下",
                ]},
            )


def _scan_content_pool(session: Session, now: datetime) -> None:
    """内容够不够分。按城市算，因为内容是按城市发的 —— 总数够不代表每个城市都够。"""
    target = daily_target(session)
    demand: dict[str, int] = {}
    homeless = []
    for account in _active_accounts(session):
        city = real_city(account.city)
        if not city:
            # 没有「未分组」这个城市。号没设城市是**漏配了**，不是一个内容分组 ——
            # 给它算库存只会凭空造出一个永远告急的假城市。单独提醒去补。
            homeless.append(account.nickname)
            continue
        demand[city] = demand.get(city, 0) + _expected_for(account, target)
    today = f"{_cn_now():%Y%m%d}"
    if homeless:
        who = "、".join(homeless[:3]) + ("等" if len(homeless) > 3 else "")
        notify.enqueue(
            event="account_no_city",
            dedup_key=f"nocity:{today}",
            title=f"⚠️ {who}还没设城市",
            severity="warning",
            payload={"lines": [
                "没有城市就分不到本地内容，只能发不限城市的那些",
                "去账号页给它补上城市",
            ]},
        )
    if not demand:
        return
    # 不限城市的内容是**所有城市一起用**的：本地内容取不完时会从这里补。
    # 只数本地会把每个没有本地内容的城市都误报成告急 —— 而这个群是运营唯一会看的
    # 地方，误报一次就会开始被忽略一次。
    from app.services.tasks import GENERIC_CITIES

    shared = session.exec(
        select(func.count())
        .select_from(ContentItem)
        .where(ContentItem.status == ContentStatus.PENDING)
        .where(ContentItem.city.in_(GENERIC_CITIES))
    ).one()
    for city, need in demand.items():
        local = session.exec(
            select(func.count())
            .select_from(ContentItem)
            .where(ContentItem.status == ContentStatus.PENDING)
            .where(ContentItem.city == city)
        ).one()
        if need <= 0:
            continue
        days = (local + shared) / need
        if days < 1:
            notify.enqueue(
                event="content_pool_critical",
                dedup_key=f"poolcrit:{city}:{today}",
                title=f"🚨 {city}的内容不够今天发了",
                severity="critical",
                city=city,
                payload={"lines": [
                    f"{city}本地内容只剩 {local} 篇，今天要发 {need} 篇",
                    f"加上 {shared} 篇不限城市的也不够，今天就会有号没内容可发",
                    "去内容页给这个城市补稿",
                ]},
            )
        elif days < 2:
            notify.enqueue(
                event="content_pool_low",
                dedup_key=f"pool:{city}:{today}",
                title=f"⚠️ {city}的内容快不够分了",
                severity="warning",
                city=city,
                payload={"lines": [
                    f"{city}本地内容只剩 {local} 篇，每天要发 {need} 篇",
                    f"加上 {shared} 篇不限城市的还能发 {days:.1f} 天，"
                    "但那些是三个城市一起用、先到先得",
                    "去内容页给这个城市补稿",
                ]},
            )


# ---------------------------------------------------------------------------
# 节奏摘要（不入队，直接发）
# ---------------------------------------------------------------------------
def _tasks_between(session: Session, since: datetime) -> list[PublishTask]:
    return session.exec(
        select(PublishTask)
        .where(PublishTask.finished_at.is_not(None))
        .where(PublishTask.finished_at >= since.replace(tzinfo=None))
    ).all()


def _send_to_all_routes(session: Session, markdown: str) -> None:
    """摘要类消息发给所有启用的渠道（它不属于任何城市）。"""
    routes = session.exec(
        select(notify.NotifyRoute).where(notify.NotifyRoute.enabled == True)  # noqa: E712
    ).all()
    for route in routes:
        if not route.target.strip():
            continue
        if route.channel == "email":
            notify.send_email(route, "[发布室] 运营摘要", markdown.replace("**", ""))
        else:
            notify.send_wecom(route, markdown, [])


def _describe(session: Session, task: PublishTask) -> tuple[str, str]:
    """(账号名, 城市)。任务可能还没派到设备，所以要回落 target_device_id。"""
    device_id = task.device_id or task.target_device_id
    account = _account_of(session, device_id, task.platform) if device_id else None
    if account and account.nickname:
        return account.nickname, real_city(account.city)
    device = session.get(Device, device_id) if device_id else None
    return (device.name if device else "未知账号"), ""


NO_CITY = "未设城市"
# 一个城市最多列几个号名。超过就给个数 —— 再多就不是"看一眼"而是"读一屏"了。
_MAX_NAMES = 10


CITY_ORDER_KEY = "notify:city_order"


def _city_order(session: Session) -> list[str]:
    """消息里每一处按城市分的地方，都用这个顺序。**顺序必须是死的。**

    以前是按当前账号数现算的。号数一增减城市就换位置，运营就永远形不成
    「我负责的那行在第几行」的肌肉记忆 —— 而这条消息是他们唯一的界面。
    所以优先读配置（settings key `notify:city_order`，逗号分隔），配置里没有的
    城市按账号数追加到末尾，新开城市不会插队。「没设城市」不进这个列表：
    它不是城市，单独一行说（见 _unset_city_note）。
    """
    counts: dict[str, int] = {}
    for account in _active_accounts(session):
        city = real_city(account.city)
        if city:
            counts[city] = counts.get(city, 0) + 1
    configured = [
        c.strip() for c in settings_store.get(session, CITY_ORDER_KEY, "").split(",")
        if c.strip()
    ]
    ordered = [c for c in configured if c in counts]
    rest = sorted((c for c in counts if c not in ordered), key=lambda c: (-counts[c], c))
    return ordered + rest


def _active_accounts(session: Session) -> list:
    """最近 ACTIVE_WINDOW_HOURS 小时内成功发过的号 —— 只有这些才归这套系统管。

    超过这个时间没发的号，是在用别的方式发布（用户确认过），提醒它们只是噪音；
    而噪音会让整个群被忽略，那才是真正的代价。
    """
    from app.services.tasks import cn_day_start_utc  # noqa: F401  (统一时间处理入口)

    since = datetime.now(timezone.utc) - timedelta(hours=ACTIVE_WINDOW_HOURS)
    lively = set(
        session.exec(
            select(PublishTask.device_id)
            .where(PublishTask.status == TaskStatus.SUCCEEDED)
            .where(PublishTask.publish_type != "group_message")
            .where(PublishTask.finished_at >= since.replace(tzinfo=None))
        ).all()
    )
    return [
        a for a in session.exec(
            select(DeviceAccount).where(DeviceAccount.platform == "douyin")
        ).all()
        if a.nickname and a.device_id in lively
    ]


def _dormant_count(session: Session) -> int:
    """有号、但都超过 48 小时没发的数量。只用来判断"是不是整个都停了"。"""
    total = len([
        a for a in session.exec(
            select(DeviceAccount).where(DeviceAccount.platform == "douyin")
        ).all() if a.nickname
    ])
    return total - len(_active_accounts(session))


def _accounts_by_city(session: Session) -> dict[str, list]:
    """城市 → 该城**还在这套系统里跑**的账号。所有分母都从这里来。"""
    buckets: dict[str, list] = {}
    for account in _active_accounts(session):
        buckets.setdefault(real_city(account.city) or NO_CITY, []).append(account)
    return buckets


def _c(text: str, color: str) -> str:
    """企微字体色（只支持 info 绿 / comment 灰 / warning 橙）。

    颜色只做**冗余**分层，不承担唯一语义：消息被转成纯文本、邮件透传、色弱用户，
    任意一种情况颜色就整个没了。所以圆点和「7/8」这种数字必须自己就读得懂。
    """
    return f'<font color="{color}">{text}</font>'


def _width(text: str) -> float:
    """显示宽度，全角算 1、半角算 0.5。企微 PC 卡片约 40 全角字符折行，手机更窄。"""
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in text)


def _fit_names(names: list[str], budget: float = 13.0) -> str:
    """名字**装得下才列**，装不下一个都不列。

    以前是"列前 6 个再加省略号"，结果那行必然折行，而且折行点落在名字中间 ——
    看起来比不列更糟，还看不出到底还剩几个。宁可只给数量。
    """
    joined = "、".join(names)
    return joined if _width(joined) <= budget else ""


def _by_city(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {}
    for who, city in pairs:
        buckets.setdefault(city or NO_CITY, []).append(who)
    return buckets


def _today_tasks(session: Session) -> list[PublishTask]:
    """今天（中国时区）这一天的活。

    ⚠ 判据**不是** created_at。运营会一次性把未来几天的排期都排出去，这些任务的
    created_at 是今天、scheduled_at 却是 09-05/06/07 —— 按创建时间算会把它们全塞进
    今天的「待发」，于是消息里同时出现「15 个号今天没排上任务」和「待发 21」，自相矛盾。
    这和 S0 修过的「未到点的排期算作正在进行」是同一类错：**排期任务属于它被排到的
    那天，不属于它被创建的那天。**

    今天的活 = 今天收尾的（不管什么时候建的）+ 还没收尾且不是排到今天之后的。
    排期已经过期却还挂着的（比如排在上个月、至今 queued）也算今天的活 —— 它确实是
    还欠着的债，藏起来只会一直欠下去。
    """
    from app.services.tasks import cn_day_start_utc

    start = cn_day_start_utc()
    end = start + timedelta(days=1)
    rows = session.exec(
        select(PublishTask).where(
            (PublishTask.finished_at >= start)
            | (PublishTask.created_at >= start)
            | (PublishTask.scheduled_at.is_not(None) & (PublishTask.scheduled_at < end))
        )
    ).all()
    today = []
    for task in rows:
        finished = task.finished_at
        if finished is not None and finished >= start:
            today.append(task)          # 今天收尾的一定算今天
            continue
        if task.status in TERMINAL_STATES:
            continue                    # 今天之前就收尾了，不是今天的活
        if task.scheduled_at is not None and task.scheduled_at >= end:
            continue                    # 排到明天以后，不是今天的活
        today.append(task)
    return today


def _progress_parts(session: Session) -> dict:
    """今日产能视图。返回各块文本，进度播报和日报各取所需。

    ⚠ 城市行**全按「号」算，不按「篇」算**。以前分子数成功任务数、分母数账号数，
    一个号发两篇就渲染成「北京 9/9 ✅」而其实还有 3 个号一篇没发 —— 这个数字会
    当场骗人。号才是运营派活的单位。
    """
    tasks = _today_tasks(session)
    posts = [t for t in tasks if t.publish_type != "group_message"]
    groups = [t for t in tasks if t.publish_type == "group_message"]

    ok = [t for t in posts if t.status == TaskStatus.SUCCEEDED]
    bad = [t for t in posts if t.status == TaskStatus.FAILED]
    dropped = [t for t in posts if t.status == TaskStatus.CANCELLED]
    waiting = [t for t in posts if t.status == TaskStatus.WAITING_CONFIRMATION]
    running = [t for t in posts if t.status in (TaskStatus.RUNNING, TaskStatus.LEASED)]
    queued = [t for t in posts if t.status == TaskStatus.QUEUED]

    by_city = _accounts_by_city(session)
    accounts = [a for group in by_city.values() for a in group]
    published_devices = {t.device_id for t in ok if t.device_id}
    done_accounts = [a for a in accounts if a.device_id in published_devices]

    # ---- 城市行：号 / 号，引用块内不用颜色（企微各端对引用里的 <font> 表现不一致，
    # 而引用文字本身就是灰的，绿橙很可能被吞掉，只剩圆点在传信号）----
    city_lines = []
    for city in _city_order(session):
        members = by_city.get(city, [])
        if not members:
            continue
        finished = [a for a in members if a.device_id in published_devices]
        missing = [a for a in members if a.device_id not in published_devices]
        if not missing:
            # 发完的城市压成一行，不列名字 —— 成功的号不需要被念一遍，
            # 单条发布播报已经逐条报过了
            city_lines.append(f"**✅ {city}**　{len(members)} 个号都发完了")
            continue
        flag = "🔴" if not finished else "⚠️"
        if not finished:
            city_lines.append(f"**{flag} {city}**　{len(members)} 个号一个都没发")
        else:
            city_lines.append(
                f"**{flag} {city}**　{len(members)} 个号发了 {len(finished)} 个，"
                f"还差 {len(missing)} 个"
            )
        # **一个账号一行**。折进城市行里运营就分辨不出是哪几个号，还得去开控制台，
        # 而"不用开控制台"正是这套通知存在的理由。
        for account in missing[:_MAX_NAMES]:
            city_lines.append(f"> {account.nickname}")
        if len(missing) > _MAX_NAMES:
            city_lines.append(f"> 还有 {len(missing) - _MAX_NAMES} 个，去控制台看")
        city_lines.append("")      # 城市块之间空一行，视觉上分开

    while city_lines and not city_lines[-1]:
        city_lines.pop()

    # ---- 「没设城市」不是城市，不进城市块 ----
    homeless = by_city.get(NO_CITY, [])

    # ---- 标题：一句话给结论，并且**必须带时间**。每 2 小时一条，数字没变时
    # 两条标题一模一样会看着像机器人重复推送，往回翻也分不清哪条是几点的。----
    goal = len(accounts)
    done = len(done_accounts)
    stamp = f"{_cn_now():%m-%d %H:%M}"
    if goal and done >= goal:
        headline = f"**今天进度：{goal} 个号都发完了** · {stamp}"
    else:
        headline = f"**今天进度：{goal} 个号发了 {done} 个** · {stamp}"

    # ---- 要动手的 ----
    touched = {t.device_id or t.target_device_id for t in tasks}
    unscheduled = [a for a in accounts if a.device_id not in touched]
    g_ok = [t for t in groups if t.status == TaskStatus.SUCCEEDED]
    g_bad = [t for t in groups if t.status == TaskStatus.FAILED]
    actions = []
    if unscheduled:
        # 说清和"还差几个"的包含关系，否则读的人会当成第三个数字
        actions.append(_c(
            f"还没发的这 {len(unscheduled)} 个号，今天都还没安排内容，去排期页排一下",
            "warning"))
    if bad:
        # ⚠ 不要写成「去排期页重新排一下」。引擎会自己补：失败不占当天名额，
        # 退避 20 分钟后自动再建一条（`tasks._due_by_plan` / `_failure_backoff`）。
        # 让运营手动再排一条 = 同一篇内容发两遍到真实账号上。
        # 这里只报数，要不要动手看单条通知里那句现算的结论。
        actions.append(_c(
            f"有 {len(bad)} 条没发出去，引擎会在后面的时段自动补，不用手动排",
            "warning"))
    if waiting:
        actions.append(_c(f"有 {len(waiting)} 条卡住了，要有人去手机上看一眼", "warning"))
    if g_bad:
        # 只说失败数会让人以为大盘正常、只是有几条要重发。分子分母一起给才是实话。
        total_g = len(g_ok) + len(g_bad)
        actions.append(_c(
            f"群发 {total_g} 条里只有 {len(g_ok)} 条发进了群", "warning"))
    if homeless:
        who = "、".join(a.nickname for a in homeless[:3])
        actions.append(_c(
            f"{who}还没设城市，只能发不限城市的内容，去账号页给它补上", "warning"))

    # ---- 灰字尾行：零值不渲染；带数据截止时间当心跳 ----
    background = [f"{label} {n} 条" for label, n in (
        ("正在发", len(running)), ("等着发", len(queued)),
        ("取消了", len(dropped)), ("群发成功", len(g_ok)),
    ) if n]
    return {
        "headline": headline,
        "city_lines": city_lines,
        "actions": actions,
        "background": _c(" · ".join(background), "comment") if background else "",
        "stock": [_c(x, "comment") for x in _stock_summary(session)],
        "goal": goal, "done": done,
    }


def _stock_summary(session: Session) -> list[str]:
    """一行说清内容够不够。

    **不给"总共够几天"** —— 不限城市的那批是三个城市一起抢的，用总数算出来的
    「够 2 天」恰好会把「北京只剩 3 篇要喂 8 个号」这唯一的真风险盖住。
    给总数 + 最缺的那个城市，运营才知道该去补哪。
    """
    from app.services.tasks import GENERIC_CITIES

    rows = session.exec(
        select(ContentItem.city, func.count())
        .where(ContentItem.status == ContentStatus.PENDING)
        .group_by(ContentItem.city)
    ).all()
    total = sum(n for _, n in rows)
    if not total:
        return ["内容库空了，先去写点内容"]
    local = {c: n for c, n in rows if (c or "") not in GENERIC_CITIES}
    shared = total - sum(local.values())
    by_city = _accounts_by_city(session)
    worst, fewest = None, None
    for city in _city_order(session):
        members = by_city.get(city, [])
        if not members:
            continue
        per_head = local.get(city, 0) / len(members)
        if fewest is None or per_head < fewest:
            worst, fewest = city, per_head
    # 一行超过约 40 个中文字就会被折成一坨，所以拆成两句、各占一行
    lines = [f"内容库 {total} 篇，其中 {shared} 篇不限城市，各城一起用"]
    if worst is not None and fewest is not None and fewest < 1:
        lines.append(
            f"{worst}最缺：本地内容 {local.get(worst, 0)} 篇，"
            f"{len(by_city[worst])} 个号一人一篇都不够"
        )
    return lines


def _stock_lines(session: Session) -> list[str]:
    """内容够不够，一个城市一行。

    说"还差几篇"而不是光给篇数 —— 「28 篇」本身不说明任何事，「够发 3 天」才是。
    不限城市的那批单独一行并写明是共用的：它被所有城市一起抢，混进城市行会让人
    高估某个城市的家底。
    """
    from app.services.tasks import GENERIC_CITIES

    rows = session.exec(
        select(ContentItem.city, func.count())
        .where(ContentItem.status == ContentStatus.PENDING)
        .group_by(ContentItem.city)
    ).all()
    total = sum(n for _, n in rows)
    if not total:
        return ["> 内容库空了，先去写点内容"]
    local = {c: n for c, n in rows if (c or "") not in GENERIC_CITIES}
    shared = total - sum(local.values())
    by_city = _accounts_by_city(session)
    lines = []
    for city in _city_order(session):
        members = by_city.get(city, [])
        if not members:
            continue
        have = local.get(city, 0)
        if have < len(members):
            lines.append(
                f"> {city} {len(members)} 个号要发，{city}本地内容 {have} 篇，"
                f"差 {len(members) - have} 篇"
            )
        else:
            lines.append(
                f"> {city} {len(members)} 个号要发，{city}本地内容 {have} 篇，"
                f"够发 {have // len(members)} 天"
            )
    lines.append(
        f"> 另有 {shared} 篇不限城市，各城一起用，差的从这里补，先到先得"
    )
    return lines


# 库里存着的**旧**失败原因（改文案之前写进去的），照原样显示会在群里露出行话。
# 周报要回看 7 天、日报回看 1 天，所以过渡期里必须在显示时翻译一遍。
# 新写入的原因已经是人话了（见 tasks.py），这张表只服务历史数据，可以随 30 天
# 数据保留期自然退休。
_LEGACY_REASONS = {
    "执行设备超时未续租，任务已自动判定失败并释放设备": "发到一半手机没了响应，这条已经自动停掉",
    "租约超时未续租，任务已自动失败并释放设备": "发到一半手机没了响应，这条已经自动停掉",
    "页面需人工处理但超时未介入，已自动跳过并释放设备": "卡在需要人工确认的页面，没人处理，已自动跳过",
    "等待人工处理超时，任务已自动失败并释放设备，队列继续": "等人处理超时，这条自动跳过，后面的继续发",
}


def _readable_reason(text: str | None) -> str:
    """失败原因 → 人话。历史数据里存的是旧措辞，显示时翻一遍。"""
    raw = (text or "").strip()
    if not raw:
        return "没记下失败的原因"
    return _LEGACY_REASONS.get(raw, raw)


def _activity_digest(session: Session, now: datetime) -> bool:
    """每 2 小时一条今日进度播报。

    单条发布成功/失败已经各自成条消息了（见 tasks.notify_task_finished），这里回答
    的是另一个问题：**今天该发的发完了没**。两者不重复：一个是流水，一个是水位。
    """
    last_raw = settings_store.get(session, _LAST_ACTIVITY_KEY, "")
    last = None
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
        except ValueError:
            last = None
    if last is None:
        last = now - timedelta(hours=ACTIVITY_DIGEST_HOURS)
    if now - last < timedelta(hours=ACTIVITY_DIGEST_HOURS):
        return False
    cn = now.astimezone(_CN_TZ)
    if not (8 <= cn.hour <= 22):
        return False  # 深夜不播报，白天的节奏才有人看
    # ⚠ 活跃名册为空 = 过去 48 小时一个号都没发过。这不是"没事所以不用发消息"，
    # 恰恰是最该喊的时候：整套系统可能已经停了。沉默永远不能表示故障。
    dormant = _dormant_count(session)
    if not _active_accounts(session):
        if dormant:
            settings_store.put(session, _LAST_ACTIVITY_KEY, now.isoformat())
            _send_to_all_routes(session, chr(10).join([
                "🚨 **过去 48 小时一个号都没发过**",
                "",
                f"{dormant} 个号全都停了，系统可能出问题了，去控制台看一眼",
            ]))
            return True
        return False

    parts = _progress_parts(session)
    # 城市块和「要动手的」之间要有一个非引用行，否则连续的 > 会合并成一整块引用，
    # 分组就看不出来了
    lines = [f"📊 {parts['headline']}", ""] + parts["city_lines"]
    if parts["actions"]:
        lines += [""] + parts["actions"]
    if parts["background"]:
        lines.append(parts["background"])
    lines += parts["stock"]
    # 戳要在**拼装成功之后**才盖：拼装过程里抛错的话，先盖戳等于把这一轮永久跳过，
    # 而 sweep 会把异常吞掉，线上什么都看不到。现在拼装出错就等下一轮（5 分钟）再试。
    settings_store.put(session, _LAST_ACTIVITY_KEY, now.isoformat())
    _send_to_all_routes(session, chr(10).join(lines))
    return True


def _daily_digest(session: Session, now: datetime) -> bool:
    """每天 09:00 一条 **昨天的成绩单**。

    标题按**它汇报的那一天**命名（「📅 09-03 日报」），不按发送日 —— 用户原话：
    「为什么日报还有昨日的内容？要么就写好几月几日日报」。一条 09-04 发出、
    标题写 09-04、身体却全是 09-03 数据的消息，谁看都别扭。
    """
    cn = now.astimezone(_CN_TZ)
    today = f"{cn:%Y%m%d}"
    if cn.hour < DIGEST_HOUR_CN:
        return False
    if settings_store.get(session, _LAST_DAILY_KEY, "") == today:
        return False

    day_start = (cn - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = cn.replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = [
        t for t in _tasks_between(session, day_start.astimezone(timezone.utc))
        if _aware(t.finished_at) < day_end
    ]
    posts = [t for t in tasks if t.publish_type != "group_message"]
    groups = [t for t in tasks if t.publish_type == "group_message"]
    ok = [t for t in posts if t.status == TaskStatus.SUCCEEDED]
    bad = [t for t in posts if t.status == TaskStatus.FAILED]

    by_city = _accounts_by_city(session)
    accounts = [a for group in by_city.values() for a in group]
    target = daily_target(session)
    published = {t.device_id for t in ok if t.device_id}
    done = len([a for a in accounts if a.device_id in published])
    silent = len(accounts) - done

    # 标题写「昨日汇总 + 那一天的日期」，不写发送日 —— 账本上的日期是记账日，
    # 不是打印日。下面用加粗小标题把两天物理切开，就不用再写一句
    # "上半段是昨天下半段是今天"的旁白：结构本身已经说清楚了。
    weekday = "一二三四五六日"[day_start.weekday()]
    lines = [f"**📅 昨日汇总 {day_start:%m-%d}（周{weekday}）**", ""]
    head = f"**{len(accounts)} 个号里发了 {done} 个**"
    if silent:
        head += f"，{silent} 个号一条没发"
    lines.append(head)
    lines.append("")

    if bad:
        detail = []
        for task in bad[:2]:
            who, city = _describe(session, task)
            why = _readable_reason(task.error_message)[:28]
            detail.append(f"{who}{'（' + city + '）' if city else ''} {why}")
        if len(bad) <= 2:
            lines.append("> 没发出去 " + str(len(bad)) + " 条：" + "；".join(detail))
        else:
            reasons: dict[str, int] = {}
            for task in bad:
                why = _readable_reason(task.error_message)[:28]
                reasons[why] = reasons.get(why, 0) + 1
            # 日频下「失败 3 条」的原因占比是伪分布（2 条同因 = 67%，明天可能 0%），
            # 所以只给最大的那一档，完整分布留给周报。
            top, count = sorted(reasons.items(), key=lambda kv: -kv[1])[0]
            lines.append(f"> 没发出去 {len(bad)} 条，最多的是「{top}」{count} 条")
    if groups:
        g_ok = len([t for t in groups if t.status == TaskStatus.SUCCEEDED])
        lines.append(f"> 群发 {len(groups)} 条：{g_ok} 条发进了群，{len(groups) - g_ok} 条一个群都没进去")
    # 原来这里有一段「连续两天一条没发」的点名。按 48 小时规则它定义上就是空的：
    # 超过 48 小时没发的号已经不在名册里了。留着只会天天打印一个 0。

    # 今天只讲**计划和弹药**，不讲完成度：09:00 的完成度必然是 0/22，
    # 把它渲染成一屏 🔴 等于每天早上发一次假警报，一周后这条消息就废了。
    # 让周五的周报不是突然袭击：运营周三就该知道这周要掉。只给总数，
    # 不拆城市、不给环比 —— 那些是周报的主角，日报里重复就成了噪音。
    # ⚠ _week_window 给的是**上一个已经走完的**周（上周五→本周四），那是周报的窗口。
    # 日报要的是"本周到现在为止"，起点是**最近一个周五**，也就是那个元组的第二个值。
    # 写成 _week_window(cn) 直接解包的话，周六到周四这六天里这行会一直显示上周的数字，
    # 一动不动 —— 看着像卡死，实际上是窗口取错了。
    _, week_start = _week_window(cn)
    week_done = _week_output(session, week_start, cn)["done"]
    week_goal = sum(_expected_for(a, target) for a in accounts) * 7
    lines.append(_c(
        f"本周已发 {week_done}/{week_goal} 篇（{week_start:%m-%d} 起）", "comment"
    ))

    parts = _progress_parts(session)
    lines += ["", f"**今天 {cn:%m-%d}：{parts['goal']} 个号要发**", ""]
    lines += _stock_lines(session)

    # 「要动手的」永远存在，没事也要写「无」—— 日报一天只有一条，必须有一句
    # 显式说没事，否则「真没事」和「渲染断了」在群里长得一模一样。
    bad_accounts = [a for a in accounts if a.health and a.health != "normal"]
    unready = [
        d for d in session.exec(select(Device)).all()
        if d.accessibility_ok is False and _account_of(session, d.id) is not None
    ]
    todo = list(parts["actions"])
    todo += [_c(f"{a.nickname} 被抖音限制了：{a.health_message or a.health}", "warning")
             for a in bad_accounts[:3]]
    todo += [_c(f"{d.name} 的自动操作权限被关了，重启这台手机", "warning")
             for d in unready[:3]]
    lines += ["", "**今天要处理**", ""]
    lines += todo if todo else [_c("没有要处理的，一切正常", "info")]
    # 拼装成功之后才盖戳（见 _activity_digest 里的说明）
    settings_store.put(session, _LAST_DAILY_KEY, today)
    _send_to_all_routes(session, chr(10).join(lines))
    return True


def _aware(moment):
    if moment is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)



# ---------------------------------------------------------------------------
# 周报
# ---------------------------------------------------------------------------
def _delta(now_value: int, was: int, unit: str = "") -> str:
    """环比措辞。差值为 0 时说「和上周持平」——「比上周多 0 条」是机器话。"""
    diff = now_value - was
    if diff == 0:
        return "和上周持平"
    return f"比上周{'多' if diff > 0 else '少'} {abs(diff)}{unit}"


def _wan(n: int) -> str:
    """播放这种大数用「万」，60604 → 6.1 万。原样打出来没人读得出量级。"""
    return f"{n / 10000:.1f} 万" if abs(n) >= 10000 else str(n)


def _week_window(cn_now: datetime) -> tuple[datetime, datetime]:
    """(上周五 00:00, 本周四 24:00)，中国时区。周五当天调用时窗口正好是刚过完的 7 天。"""
    # 本周五 00:00（今天就是周五时即今天）
    days_since_friday = (cn_now.weekday() - WEEKLY_WEEKDAY) % 7
    this_friday = (cn_now - timedelta(days=days_since_friday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return this_friday - timedelta(days=7), this_friday


def _naive(moment: datetime) -> datetime:
    """中国时间 → 库里存的 naive UTC。库里全是剥掉 tzinfo 的 UTC 字符串。"""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _posts_in(session: Session, start: datetime, end: datetime) -> list[PublishTask]:
    return session.exec(
        select(PublishTask)
        .where(PublishTask.finished_at >= _naive(start))
        .where(PublishTask.finished_at < _naive(end))
    ).all()


def _week_output(session: Session, start: datetime, end: datetime) -> dict:
    """一个 7 天窗口的产能。周报和它的环比用的是同一个函数，保证两组数可比。"""
    tasks = _posts_in(session, start, end)
    posts = [t for t in tasks if t.publish_type != "group_message"]
    groups = [t for t in tasks if t.publish_type == "group_message"]
    ok = [t for t in posts if t.status == TaskStatus.SUCCEEDED]
    per_device: dict[int, int] = {}
    for task in ok:
        if task.device_id:
            per_device[task.device_id] = per_device.get(task.device_id, 0) + 1
    return {
        "done": len(ok),
        "failed": [t for t in posts if t.status == TaskStatus.FAILED],
        "cancelled": [t for t in posts if t.status == TaskStatus.CANCELLED],
        "groups_ok": len([t for t in groups if t.status == TaskStatus.SUCCEEDED]),
        "groups_all": len(groups),
        "per_device": per_device,
    }


def _engagement_delta(session: Session, start: datetime, end: datetime) -> dict:
    """本周互动/播放增量，**只算窗口开始前就有底数的作品**。

    ⚠ PostMetric 是「数值变了才写一行」的快照表。基线必须取 captured_at < 窗口起点的
    最后一行（as-of），绝不能拿窗口内的第一行当基线 —— 那是"这周第一次看到它"，
    不是"上周末的值"。实测某一周：252 条作品里 80 条没有真基线，把它们按
    「全量 = 增量」算的话，9255 的互动增量里有 7702（83%）是假的，而且是随机地假
    （哪台机这周恰好第一次回采成功就虚高哪台）。所以两个数分开报，不合成一个。
    """
    rows = session.exec(
        select(PostMetric)
        .where(PostMetric.captured_at < _naive(end))
        .where(PostMetric.platform == "douyin")
        .order_by(PostMetric.captured_at)
    ).all()
    start_naive = _naive(start)
    base: dict[tuple, PostMetric] = {}
    last: dict[tuple, PostMetric] = {}
    for row in rows:
        key = (row.device_id, row.task_id or row.title)
        if row.captured_at < start_naive:
            base[key] = row
        last[key] = row

    def score(m: PostMetric) -> int:
        return m.likes + m.collects + m.comments

    per_device: dict[int, int] = {}
    engagement = views = 0
    for key, tail in last.items():
        head = base.get(key)
        if head is None:
            continue                      # 没底数的不算，另行说明覆盖度
        gain = score(tail) - score(head)
        engagement += gain
        views += tail.views - head.views
        if tail.device_id:
            per_device[tail.device_id] = per_device.get(tail.device_id, 0) + gain
    fresh = len([k for k in last if k not in base])
    seen = session.exec(
        select(PostMetric.device_id, func.max(PostMetric.captured_at))
        .where(PostMetric.platform == "douyin")
        .group_by(PostMetric.device_id)
    ).all()
    return {
        "engagement": engagement, "views": views, "with_base": len(base),
        "fresh": fresh, "per_device": per_device,
        "last_seen": {d: t for d, t in seen if d is not None},
    }


def _weekly_digest(session: Session, now: datetime) -> bool:
    """每周五 09:30 一条复盘凭证。

    标题写**窗口**不写发送日 —— 和日报同一条规则：凭证的日期等于账目日期，
    不等于打印日期。周五发的周报如果标题写 09-04，犯的是一模一样的错。
    """
    cn = now.astimezone(_CN_TZ)
    if cn.weekday() != WEEKLY_WEEKDAY:
        return False
    if (cn.hour, cn.minute) < (WEEKLY_HOUR_CN, WEEKLY_MINUTE_CN):
        return False
    year, week, _ = cn.isocalendar()
    tag = f"{year}-W{week:02d}"
    if settings_store.get(session, _LAST_WEEKLY_KEY, "") == tag:
        return False

    start, end = _week_window(cn)
    prev_start, prev_end = start - timedelta(days=7), start
    this = _week_output(session, start, end)
    prev = _week_output(session, prev_start, prev_end)

    by_city = _accounts_by_city(session)
    accounts = [a for group in by_city.values() for a in group]
    target = daily_target(session)
    goal = sum(_expected_for(a, target) for a in accounts) * 7
    rate = (this["done"] / goal * 100) if goal else 0.0
    prev_rate = (prev["done"] / goal * 100) if goal else 0.0

    lines = [f"**📅 周报 {start:%m-%d} ~ {end - timedelta(days=1):%m-%d}**", ""]
    lines.append(f"**发了多少：{this['done']}/{goal} 篇，完成 {rate:.0f}%**")
    # 每个数字都得挂一个对比项 —— 「本周发了 118 篇」单独摆着没有信息量，
    # 「比上周少 13 篇、达成率掉 8 个点」才有。
    lines.append(
        _delta(this["done"], prev["done"], " 篇")
        + f"（上周 {prev['done']} 篇，完成 {prev_rate:.0f}%）"
    )
    lines.append("")
    for city in _city_order(session):
        members = by_city.get(city, [])
        if not members:
            continue
        city_goal = sum(_expected_for(a, target) for a in members) * 7
        got = sum(this["per_device"].get(a.device_id, 0) for a in members)
        was = sum(prev["per_device"].get(a.device_id, 0) for a in members)
        pct = (got / city_goal * 100) if city_goal else 0
        dot = "✅" if pct >= 90 else ("⚠️" if pct >= 75 else "🔴")
        delta = got - was
        lines.append(
            f"> {dot} {city} {got}/{city_goal} 篇，完成 {pct:.0f}%，{_delta(got, was, ' 篇')}"
        )

    # 掉队的号：本周产出不到目标一半，或连续两周挂零
    laggards = []
    for city in _city_order(session) + [NO_CITY]:
        for account in by_city.get(city, []):
            got = this["per_device"].get(account.device_id, 0)
            was = prev["per_device"].get(account.device_id, 0)
            want = _expected_for(account, target) * 7
            label = city if city != NO_CITY else "没设城市"
            if got == 0 and was == 0:
                laggards.append((0, f"> {label}·{account.nickname} 0 篇，连续两周一条没发"))
            elif want and got * 2 < want:
                laggards.append(
                    (1, f"> {label}·{account.nickname} {got}/{want} 篇，{_delta(got, was, ' 篇')}")
                )
    if laggards:
        lines += ["", "**发得最少的号**", ""]
        lines += [text for _, text in sorted(laggards)[:8]]

    # 二、效果 —— 三档里唯一放效果数据的地方（回采一天最多一次，放日报一半是 +0）
    eff = _engagement_delta(session, start, end)
    prev_eff = _engagement_delta(session, prev_start, prev_end)
    lines += ["", f"**涨了多少：点赞、收藏、评论这周一共多了 {eff['engagement']}**", ""]
    lines.append(
        _delta(eff["engagement"], prev_eff["engagement"])
        + f"，这批作品的播放量多了 {_wan(eff['views'])}"
    )
    ranked = sorted(eff["per_device"].items(), key=lambda kv: -kv[1])[:3]
    name_of = {a.device_id: (a, c) for c, g in by_city.items() for a in g}
    if ranked:
        lines.append("")
        for device_id, gain in ranked:
            entry = name_of.get(device_id)
            if not entry:
                continue
            account, city = entry
            seen = eff["last_seen"].get(device_id)
            # 每行效果数据都要带上"数据更新到哪天"：这个号一周只有一两天能读到数据，
            # 不写清楚的话运营会拿一个压根没读到数的号去问责。
            when = f"数据更新到 {seen:%m-%d}" if seen else "还没读到过数据"
            label = city if city != NO_CITY else "没设城市"
            lines.append(f"> {label}·{account.nickname} 多了 {gain}，{when}")
    lines.append("")
    lines.append(
        f"> 上周就在的 {eff['with_base']} 条作品，一共多了 {eff['engagement']}"
    )
    if eff["fresh"]:
        lines.append(
            f"> 这周新拿到数据的 {eff['fresh']} 条作品，没有上周的数能比，没算进上面这个数"
        )
    # 「互动增量 0」有两种完全不同的含义，必须说清楚哪些号压根没回采到，
    # 否则运营会拿一个没回采到的号去问责
    never = [
        (a.nickname, c) for c, g in by_city.items() for a in g
        if a.device_id not in eff["last_seen"]
        or eff["last_seen"][a.device_id] < _naive(start)
    ]
    if never:
        lines.append(f"> 有 {len(never)} 个号整周没拿到数据，不代表它们没涨：")
        lines.append("> " + "、".join(n for n, _ in never[:6]))

    # 三、异常 —— 完整的失败原因分布只在周报（日频下的占比是伪分布）
    lines += ["", f"**出了什么问题：没发出去 {len(this['failed'])} 条，"
                  f"{_delta(len(this['failed']), len(prev['failed']), ' 条')}**", ""]
    reasons: dict[str, int] = {}
    for task in this["failed"]:
        key = _readable_reason(task.error_message)[:28]
        reasons[key] = reasons.get(key, 0) + 1
    for why, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:5]:
        lines.append(f"> {why} {count} 条")
    if this["cancelled"]:
        lines.append(
            f"> 另有 {len(this['cancelled'])} 条取消了：有人撤掉，"
            "或者被后来重排的内容换掉，不算失败"
        )
    if this["groups_all"]:
        # 「至少发出去了一个群」是如实措辞：Agent 的收尾逻辑是 sent > 0 就记成功，
        # 一条发 5 个群只成功 1 个也算 succeeded，所以这**不是到达率**。
        # 如实写：Agent 只要有一个群成功就记成功，所以这不是"全部送达"
        missed = this["groups_all"] - this["groups_ok"]
        lines.append(
            f"> 群发 {this['groups_all']} 条：{this['groups_ok']} 条发进了群，"
            f"{missed} 条一个群都没进去"
        )

    # 四、内容与 AI —— 只给**流量**不给存量。存量播报和日报天天在说。
    inflow = session.exec(
        select(func.count()).select_from(ContentItem)
        .where(ContentItem.created_at >= _naive(start))
        .where(ContentItem.created_at < _naive(end))
    ).one()
    used = session.exec(
        select(func.count()).select_from(ContentItem)
        .where(ContentItem.published_at.is_not(None))
        .where(ContentItem.published_at >= _naive(start))
        .where(ContentItem.published_at < _naive(end))
    ).one()
    lines += ["", "**内容够不够**", ""]
    net = inflow - used
    lines.append(
        f"> 这周新写 {inflow} 篇，发掉 {used} 篇，"
        + (f"写的比发的多 {net} 篇，内容库在变多" if net >= 0
           else f"写的比发的少 {abs(net)} 篇，内容库在变少")
    )
    try:
        from app.models.entities import ContentDraft

        drafts = session.exec(
            select(ContentDraft)
            .where(ContentDraft.created_at >= _naive(start))
            .where(ContentDraft.created_at < _naive(end))
        ).all()
        if drafts:
            accepted = len([d for d in drafts if d.status == "accepted"])
            dup = len([d for d in drafts if d.status == "duplicate"])
            cost = sum(d.cost_cny or 0 for d in drafts)
            lines.append(
                f"> AI 写了 {len(drafts)} 篇，用了 {accepted} 篇，"
                f"跟已有内容重复被退回 {dup} 篇，花了 {cost:.1f} 元"
            )
    except Exception:  # noqa: BLE001 — AI 那段取不到不该拖垮整条周报
        pass

    # 原来这里有两行"口径说明"——那是给写文档的人看的，不是给收通知的人看的。
    # 该说清的事已经就近说在各自那一行里了。
    lines.append("")
    lines.append(_c(f"按当前在用的 {len(accounts)} 个号算", "comment"))
    # 拼装成功之后才盖戳（见 _activity_digest 里的说明）
    settings_store.put(session, _LAST_WEEKLY_KEY, tag)
    _send_to_all_routes(session, chr(10).join(lines))
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def sweep(session: Session) -> None:
    """由后台 loop 每 5 分钟调一次。每一段都自己兜异常，一段挂掉不影响其余。"""
    now = datetime.now(timezone.utc)
    for step in (
        lambda: _scan_devices(session, now),
        lambda: _scan_content_pool(session, now),
        lambda: _activity_digest(session, now),
        lambda: _daily_digest(session, now),
        lambda: _weekly_digest(session, now),
    ):
        try:
            step()
        except Exception:  # noqa: BLE001 — 通知永远不能影响主服务
            session.rollback()
