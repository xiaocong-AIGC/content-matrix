import json
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, func, select

from app.core.config import get_settings
from app.core.enums import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    ContentStatus,
    DeviceStatus,
    TaskStatus,
)
from app.models.entities import (
    ContentItem,
    Device,
    DeviceAccount,
    ExecutionLog,
    PublishTask,
    Screenshot,
    utcnow,
)
from app.schemas.dto import AgentLogCreate, AgentStatusUpdate, TaskCreate

# Serializes task claims within this process so two devices polling at the same
# time can never lease the same queued task (SQLite has no row-level locking).
_claim_lock = threading.Lock()

# China day boundary for the "N posts per day" quota, and the minimum gap
# between two auto-published posts on the same account (basic anti-spam).
_CN_TZ = timezone(timedelta(hours=8))
AUTO_MIN_INTERVAL = timedelta(hours=3)

# Content with these city tags is usable by any account regardless of its city.
GENERIC_CITIES = ["通用", "全国", ""]

_ACTIVE_TASK_STATUSES = [
    TaskStatus.QUEUED,
    TaskStatus.LEASED,
    TaskStatus.RUNNING,
    TaskStatus.WAITING_CONFIRMATION,
]


def _not_scheduled_ahead(now: datetime):
    """「正在进行」的并发闸必须排除**还没到点**的排期任务。

    QUEUED 里混着两种任务：马上就能派的，和排在明天 09:00 的。以前两者都被算成
    "正在进行"，后果是：给某台机排一条明天的内容，这台机**今天到明天 09:00 之间的
    自动发布 100% 静默掐死**；矩阵闸 cap 默认只有 3，矩阵里躺 3 条明天的排期就能把
    全矩阵的自动发布永久卡死。而批量排期弹窗的默认时段恰恰就是「明天 09:00」。
    口径与 claim_next_task 里的派单条件保持一致。
    """
    return (PublishTask.scheduled_at.is_(None)) | (PublishTask.scheduled_at <= now)


def purge_old_data(session: Session, days: int) -> int:
    """Delete finished tasks (+ their logs/screenshots and screenshot files) older
    than `days`, so DB/disk stay bounded. Returns how many tasks were purged."""
    if days <= 0:
        return 0
    import os

    cutoff = utcnow() - timedelta(days=days)
    old = session.exec(
        select(PublishTask)
        .where(PublishTask.status.in_(list(TERMINAL_STATUSES)))
        .where(PublishTask.created_at < cutoff)
    ).all()
    if not old:
        return 0
    ids = [t.id for t in old]
    shots = session.exec(
        select(Screenshot).where(Screenshot.task_id.in_(ids))
    ).all()
    for shot in shots:
        try:
            os.remove(shot.file_path)
        except OSError:
            pass
        session.delete(shot)
    for log in session.exec(
        select(ExecutionLog).where(ExecutionLog.task_id.in_(ids))
    ).all():
        session.delete(log)
    for task in old:
        session.delete(task)
    session.commit()
    return len(old)


def cn_day_start_utc() -> datetime:
    """Naive-UTC timestamp of the most recent China (UTC+8) midnight."""
    now_cn = datetime.now(_CN_TZ)
    start_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_cn.astimezone(timezone.utc).replace(tzinfo=None)


def count_today_published(
    session: Session, device_id: int, platform: str = "douyin"
) -> int:
    """Today's successful *posts* (作品) for one account (device + platform).
    Group messages (群发) don't count toward the per-account daily quota."""
    return session.exec(
        select(func.count())
        .select_from(PublishTask)
        .where(PublishTask.device_id == device_id)
        .where(PublishTask.platform == platform)
        .where(PublishTask.status == TaskStatus.SUCCEEDED)
        .where(PublishTask.publish_type != "group_message")
        .where(PublishTask.finished_at >= cn_day_start_utc())
    ).one()


def _finalize_content(session: Session, task: PublishTask, succeeded: bool) -> None:
    """A task that consumed pooled content reached a terminal state. Re-evaluate
    from ALL tasks of this content (a `both` content fans out to 2 — 抖音 + 小红书):
    PUBLISHED if ANY succeeded, back to the pool only if ALL failed, else still
    PUBLISHING while a sibling runs."""
    if not task.content_id:
        return
    content = session.get(ContentItem, task.content_id)
    if not content:
        return
    now = utcnow()
    siblings = session.exec(
        select(PublishTask).where(PublishTask.content_id == content.id)
    ).all()
    winner = next((t for t in siblings if t.status == TaskStatus.SUCCEEDED), None)
    all_terminal = all(t.status in TERMINAL_STATUSES for t in siblings)
    if winner is not None:
        # Live on at least one platform → PUBLISHED (attribute to the winner).
        was_pooled = content.status != ContentStatus.PUBLISHED
        content.status = ContentStatus.PUBLISHED
        content.published_device_id = winner.device_id
        content.published_task_id = winner.id
        content.published_at = content.published_at or now
        if was_pooled:
            # 库存真的少了一篇 —— 这才是"水位下降"这件事本身。补货由它触发，
            # 而不是等一个定时器隔半小时想起来看一眼。
            from app.services import supply_trigger

            supply_trigger.poke(f"{content.city or '通用'}发出去一篇")
    elif all_terminal:
        # Every fan-out task failed/cancelled → return to the pool.
        content.status = ContentStatus.PENDING
        content.published_device_id = None
        content.published_task_id = None
        content.published_at = None
    else:
        # A sibling is still running — keep it out of the pool.
        content.status = ContentStatus.PUBLISHING
    content.updated_at = now
    session.add(content)
    session.commit()


PUBLISH_WINDOWS_KEY = "publish:windows"


def publish_windows(session: Session) -> list[tuple[int, int]]:
    """自动发布的时间段。空 = 不限制（默认），手机空了就发，也就是现在的行为。"""
    from app.services import settings_store
    from app.services.schedule_windows import parse_windows

    return parse_windows(settings_store.get(session, PUBLISH_WINDOWS_KEY, ""))


def _due_by_plan(session: Session, account: "DeviceAccount", now: datetime) -> bool:
    """按时间段算：现在该不该给这个号再发一条。

    没配时间段就放行。配了的话，算出今天到现在为止计划中的时刻有几个，
    和今天已经建了几条任务比 —— 已经够了就先等下一个点。

    用的是和群推送同一套 `schedule_windows`（用户明确说"发布也是"，所以不做两套），
    随机时刻可复现，所以这个判断反复调结果一致，不需要额外的状态。
    """
    slots = publish_windows(session)
    if not slots:
        return True
    from app.services.notify_sweep import _expected_for, daily_target
    from app.services.schedule_windows import due_times

    want = _expected_for(account, daily_target(session))
    if want <= 0:
        return False
    due = due_times(
        windows=slots, count=want, now=now, key=f"publish:{account.id}"
    )
    if not due:
        return False
    # ⚠ **失败和取消的不算数**。以前这里数的是"今天建过的所有任务"，不看状态：
    # 10:30 那条失败了，它照样占着今天的一个名额，于是那一篇永远补不回来 ——
    # 当天就是少发一篇，而运营看到的只是"今天差一篇"，不知道是哪一条、为什么。
    # 现在失败会把名额吐出来，后面的时间段自动把它补上（配套的退避见
    # `_failure_backoff`：吐出名额而不退避，一台坏手机会在同一个时段里反复重试）。
    made_today = session.exec(
        select(func.count())
        .select_from(PublishTask)
        .where(PublishTask.target_device_id == account.device_id)
        .where(PublishTask.platform == account.platform)
        .where(PublishTask.publish_type != "group_message")
        .where(PublishTask.created_at >= cn_day_start_utc())
        .where(PublishTask.status.notin_([TaskStatus.FAILED, TaskStatus.CANCELLED]))
    ).one()
    return made_today < len(due)


# 失败之后等多久再试。第一次 20 分钟，之后翻倍，封顶 2 小时。
FAIL_BACKOFF_BASE = timedelta(minutes=20)
FAIL_BACKOFF_MAX = timedelta(hours=2)
# 当天连续失败到这个次数就不再重试了 —— 再试也是白试，还占着并发名额。
FAIL_GIVE_UP = 4


def _failure_backoff(
    session: Session, account: "DeviceAccount", now: datetime
) -> bool:
    """这个号刚失败过、还没到该重试的时候吗？True = 先别派任务。

    没有这道闸的话，「失败不占名额」会变成「一台坏手机在同一个时间段里反复重试」：
    每次 4 秒的 tick 都重新派一条、每条又失败，全矩阵只有 3 个并发名额
    （max_concurrent_publishes），**3 台这样的手机就能把所有号的产能压到零**，
    而且控制台上全绿 —— 设备在线、没有报错、只是"今天发了 0 篇"。

    取消的不算失败（那是人主动停的），跳过不看。
    """
    rows = session.exec(
        select(PublishTask)
        .where(PublishTask.target_device_id == account.device_id)
        .where(PublishTask.platform == account.platform)
        .where(PublishTask.publish_type != "group_message")
        .where(PublishTask.created_at >= cn_day_start_utc())
        .where(PublishTask.status.in_([TaskStatus.SUCCEEDED, TaskStatus.FAILED]))
        .order_by(PublishTask.finished_at.desc())
    ).all()
    streak = 0
    last_failed_at = None
    for task in rows:
        if task.status != TaskStatus.FAILED:
            break              # 碰到一次成功就断了，从那之后重新数
        streak += 1
        if last_failed_at is None:
            last_failed_at = task.finished_at
    if not streak or last_failed_at is None:
        return False
    if streak >= FAIL_GIVE_UP:
        return True            # 今天不用再试了
    if last_failed_at.tzinfo is None:
        last_failed_at = last_failed_at.replace(tzinfo=timezone.utc)
    wait = min(FAIL_BACKOFF_BASE * (2 ** (streak - 1)), FAIL_BACKOFF_MAX)
    return now - last_failed_at < wait


def maybe_generate_auto_task(session: Session, account: "DeviceAccount") -> None:
    """Pull-based generation for ONE account (device + platform): if auto-publish
    is on, the account is healthy and under its daily quota (and spaced out), take
    the next pooled content for that platform and queue it. The phone does one job
    at a time, so we still block on ANY active task on the device."""
    if not account.auto_publish or account.health != "normal":
        return
    now = datetime.now(timezone.utc)

    # 刚失败过就先等一会儿再试（20 分钟起、翻倍、封顶 2 小时；当天连续 4 次就不试了）。
    # 这道闸和 `_due_by_plan` 里"失败不占名额"是一对，必须同时存在。
    if _failure_backoff(session, account, now):
        return

    # 配了时间段就按计划走：每天 N 条、每条落在段内一个随机时刻。没配则放行。
    if not _due_by_plan(session, account, now):
        return

    # One job per phone at a time (any platform).
    existing = session.exec(
        select(PublishTask)
        .where(PublishTask.target_device_id == account.device_id)
        .where(PublishTask.status.in_(_ACTIVE_TASK_STATUSES))
        .where(_not_scheduled_ahead(now))
    ).first()
    if existing:
        return

    # 错峰：限制全矩阵同时进行的自动发布数——单 PC 编排器串行拉 App，太多并发会
    # 过载（lease 超时）且形成同时段规律行为。已在跑的够多就先不派，等下一轮。
    from app.core.config import get_settings

    cap = get_settings().max_concurrent_publishes
    if cap > 0:
        active_publishes = session.exec(
            select(PublishTask)
            .where(PublishTask.status.in_(_ACTIVE_TASK_STATUSES))
            .where(PublishTask.publish_type != "group_message")
            .where(_not_scheduled_ahead(now))
        ).all()
        if len(active_publishes) >= cap:
            return

    # ⚠ 每天发几篇必须和别处**同一条规则**：min(全局目标, 该号上限)。
    # 这里以前直接用 account.daily_quota，而通知和首页用的是 notify_sweep._expected_for
    # 的 min(...)。线上 daily_quota=2、全局目标=1，一旦打开自动发布，引擎会按 2 篇发、
    # 首页按 1 篇算目标，第一天就是「今日已发 28 / 14」——又一次"同一件事两个口径"。
    # 因为自动发布从来没在生产上开过，这个洞一直没暴露。
    from app.services.notify_sweep import _expected_for, daily_target

    quota = _expected_for(account, daily_target(session))
    if count_today_published(session, account.device_id, account.platform) >= quota:
        return

    last = session.exec(
        select(PublishTask)
        .where(PublishTask.target_device_id == account.device_id)
        .where(PublishTask.platform == account.platform)
        .where(PublishTask.status == TaskStatus.SUCCEEDED)
        .where(PublishTask.finished_at.is_not(None))
        .order_by(PublishTask.finished_at.desc())
    ).first()
    if last and last.finished_at:
        finished = last.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        if now - finished < AUTO_MIN_INTERVAL:
            return

    consume_content_into_task(session, account)


def _match_content(
    session: Session, platform: str, city: str
) -> "ContentItem | None":
    """Pick the oldest pending content for a platform: prefer the account's own
    city, else generic (通用/全国). Content tagged `both` is eligible for either
    platform. Never picks another city's content."""
    platforms = [platform, "both"]
    item = session.exec(
        select(ContentItem)
        .where(ContentItem.status == ContentStatus.PENDING)
        .where(ContentItem.platform.in_(platforms))
        .where(ContentItem.city == city)
        .order_by(ContentItem.created_at)
    ).first()
    if item:
        return item
    return session.exec(
        select(ContentItem)
        .where(ContentItem.status == ContentStatus.PENDING)
        .where(ContentItem.platform.in_(platforms))
        .where(ContentItem.city.in_(GENERIC_CITIES))
        .order_by(ContentItem.created_at)
    ).first()


def _build_task(
    content: ContentItem,
    account: "DeviceAccount",
    scheduled_at: datetime | None,
) -> PublishTask:
    """One auto-publish task for an account from a pooled content item."""
    return PublishTask(
        name=content.title or content.cover_title or content.body[:20] or "自动发布内容",
        platform=account.platform,
        publish_type="text",
        cover_title=content.cover_title,
        publish_title=content.title,
        body=content.body,
        topics_json=content.topics_json,
        publish_mode="auto_publish",
        target_device_id=account.device_id,
        content_id=content.id,
        scheduled_at=scheduled_at,
    )


def _find_publish_account(
    session: Session,
    platform: str,
    city: str,
    prefer_device: int | None = None,
) -> "DeviceAccount | None":
    """Pick a healthy account on `platform` to fan a `both` content out to — prefer
    the SAME device (both accounts on one phone), then the same city, then a
    generic-city account, then any. None if no such account exists."""
    accts = session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.platform == platform)
        .where(DeviceAccount.health == "normal")
    ).all()
    if not accts:
        return None

    def rank(a: "DeviceAccount") -> tuple:
        return (
            0 if a.device_id == prefer_device else 1,
            0 if a.city == city else (1 if a.city in GENERIC_CITIES else 2),
            a.id or 0,
        )

    return sorted(accts, key=rank)[0]


def consume_content_into_task(
    session: Session,
    account: "DeviceAccount",
    scheduled_at: datetime | None = None,
    content_id: int | None = None,
) -> PublishTask | None:
    """Take a pending content matching this account's platform+city and queue it as
    an auto-publish task. If `content_id` is given (operator picked a specific piece
    in the 排期 dialog) use THAT item when it's still pending+compatible; otherwise
    fall back to the oldest matching pending item. If the content is tagged `both`,
    FAN IT OUT — also queue a task on a matching account of the OTHER platform, so one
    piece is cross-posted to 抖音 AND 小红书. The two tasks run independently (one
    failing does not stop the other). None if the pool is empty.

    Selection is a lock: the chosen item flips to PUBLISHING here, so it immediately
    leaves every other account's pool (and picker) — no two accounts get the same piece.
    """
    now = utcnow()
    content = None
    if content_id is not None:
        picked = session.get(ContentItem, content_id)
        if (
            picked
            and picked.status == ContentStatus.PENDING
            and picked.platform in (account.platform, "both")
        ):
            content = picked
    if content is None:
        content = _match_content(session, account.platform, account.city)
    if not content:
        return None

    content.status = ContentStatus.PUBLISHING
    content.published_device_id = account.device_id
    content.updated_at = now
    session.add(content)

    # The consuming account always gets a task; `both` content also fans out to a
    # matching account on the other platform.
    targets: list["DeviceAccount"] = [account]
    if content.platform == "both":
        other = "xhs" if account.platform == "douyin" else "douyin"
        other_acct = _find_publish_account(
            session, other, account.city, prefer_device=account.device_id
        )
        if other_acct:
            targets.append(other_acct)

    tasks = [_build_task(content, a, scheduled_at) for a in targets]
    for task in tasks:
        session.add(task)
    session.commit()
    for task in tasks:
        session.refresh(task)
    content.published_task_id = tasks[0].id  # primary; siblings tracked by content_id
    session.add(content)
    session.commit()

    when = "到点自动发布" if scheduled_at else "自动发布"
    fan = len(tasks) > 1
    for task, acct in zip(tasks, targets):
        suffix = "（跨平台扇出）" if fan else ""
        append_log(
            session, task.id, acct.device_id, "info", "queued",
            f"{when}：从内容库取「{content.title or '无标题'}」生成 {acct.platform} 任务{suffix}",
        )
    return tasks[0]


def retry_task(session: Session, task: PublishTask) -> PublishTask:
    """Re-queue a failed/cancelled task as a fresh task (keeping the original as
    history). Re-reserves the same pooled content if any.
    """
    if TaskStatus(task.status) not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="只有失败或取消的任务可以重发")
    new_task = PublishTask(
        name=task.name,
        publish_type=task.publish_type,
        cover_title=task.cover_title,
        publish_title=task.publish_title,
        body=task.body,
        topics_json=task.topics_json,
        media_json=task.media_json,
        publish_mode=task.publish_mode,
        target_device_id=task.target_device_id,
        content_id=task.content_id,
        # 群发专属字段——之前漏拷贝，导致重发的群推送变成「发到 0 个群」(无@、无图)。
        target_groups_json=task.target_groups_json,
        mention_all=task.mention_all,
        image_path=task.image_path,
        platform=task.platform,
    )
    session.add(new_task)
    session.commit()
    session.refresh(new_task)
    if task.content_id:
        content = session.get(ContentItem, task.content_id)
        if content and content.status in {ContentStatus.PENDING, ContentStatus.PUBLISHING}:
            content.status = ContentStatus.PUBLISHING
            content.published_device_id = task.target_device_id
            content.published_task_id = new_task.id
            content.published_at = None
            content.updated_at = utcnow()
            session.add(content)
            session.commit()
    append_log(
        session,
        new_task.id,
        task.target_device_id,
        "info",
        "queued",
        f"重发任务（来自 #{task.id}）",
    )
    session.refresh(new_task)
    return new_task


def _as_naive_utc(moment: datetime) -> datetime:
    """Normalize an incoming (possibly tz-aware) datetime to naive UTC, the form
    the rest of the code and SQLite round-trips use."""
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _cn_day_bounds_utc(moment_utc: datetime) -> tuple[datetime, datetime]:
    """[start, end) naive-UTC bounds of the China (UTC+8) calendar day that the
    given naive-UTC moment falls in."""
    cn = moment_utc.replace(tzinfo=timezone.utc).astimezone(_CN_TZ)
    start_cn = cn.replace(hour=0, minute=0, second=0, microsecond=0)
    end_cn = start_cn + timedelta(days=1)
    return (
        start_cn.astimezone(timezone.utc).replace(tzinfo=None),
        end_cn.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _count_day_committed(
    session: Session,
    device_id: int,
    platform: str,
    day_start: datetime,
    day_end: datetime,
) -> int:
    """Posts already published *or* still scheduled/active for that China day for
    one account (device + platform) — i.e. how much quota is already spoken for."""
    published = session.exec(
        select(func.count())
        .select_from(PublishTask)
        .where(PublishTask.device_id == device_id)
        .where(PublishTask.platform == platform)
        .where(PublishTask.status == TaskStatus.SUCCEEDED)
        .where(PublishTask.publish_type != "group_message")
        .where(PublishTask.finished_at >= day_start)
        .where(PublishTask.finished_at < day_end)
    ).one()
    scheduled = session.exec(
        select(func.count())
        .select_from(PublishTask)
        .where(PublishTask.target_device_id == device_id)
        .where(PublishTask.platform == platform)
        .where(PublishTask.status.in_(_ACTIVE_TASK_STATUSES))
        .where(PublishTask.publish_type != "group_message")
        .where(PublishTask.scheduled_at >= day_start)
        .where(PublishTask.scheduled_at < day_end)
    ).one()
    return published + scheduled


def schedule_posts(
    session: Session,
    account: "DeviceAccount",
    times: list[datetime],
    content_ids: list[int | None] | None = None,
) -> dict:
    """Schedule auto-publish posts for ONE account at arbitrary day+time slots.
    Each slot consumes one pending content item matching the account's
    platform+city; the account's daily quota is enforced per China calendar day.

    `content_ids` (optional, parallel to `times`) lets the operator pin a specific
    content piece to a slot; a None/missing entry means "random from the pool".
    A pinned piece is locked out of other accounts once scheduled.
    """
    # Pair each time with its optional pinned content_id BEFORE sorting, so the
    # pin follows its slot regardless of chronological reordering.
    pins = content_ids or []
    paired = [
        (t, pins[i] if i < len(pins) else None) for i, t in enumerate(times)
    ]
    paired.sort(key=lambda p: p[0])
    if account.health != "normal":
        raise HTTPException(
            status_code=409,
            detail=f"账号当前状态异常（{account.health}），已暂停发布，请先处理后恢复",
        )
    quota = account.daily_quota or 2
    room_by_day: dict[datetime, int] = {}
    created = 0
    skipped_quota = 0
    pool_exhausted = False
    for moment, pinned_id in paired:
        m = _as_naive_utc(moment)
        day_start, day_end = _cn_day_bounds_utc(m)
        if day_start not in room_by_day:
            room_by_day[day_start] = max(
                0,
                quota
                - _count_day_committed(
                    session, account.device_id, account.platform, day_start, day_end
                ),
            )
        if room_by_day[day_start] <= 0:
            skipped_quota += 1
            continue
        if (
            consume_content_into_task(
                session, account, scheduled_at=m, content_id=pinned_id
            )
            is None
        ):
            pool_exhausted = True
            break  # pool exhausted — stop, remaining slots can't be filled
        room_by_day[day_start] -= 1
        created += 1
    return {
        "scheduled": created,
        "requested": len(times),
        "skipped_quota": skipped_quota,
        "pool_exhausted": pool_exhausted,
        "daily_quota": quota,
    }


def create_task(session: Session, payload: TaskCreate) -> PublishTask:
    if payload.target_device_id is not None and not session.get(
        Device, payload.target_device_id
    ):
        raise HTTPException(status_code=404, detail="指定的执行设备不存在")
    task = PublishTask(
        name=payload.name,
        platform=payload.platform,
        publish_type=payload.publish_type,
        cover_title=payload.cover_title,
        publish_title=payload.publish_title,
        body=payload.body,
        topics_json=json.dumps(payload.topics, ensure_ascii=False),
        media_json=json.dumps(payload.media, ensure_ascii=False),
        publish_mode=payload.publish_mode,
        priority=payload.priority,
        target_device_id=payload.target_device_id,
        scheduled_at=payload.scheduled_at,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    append_log(session, task.id, None, "info", "queued", "已排好，等手机来取")
    session.refresh(task)
    return task


def append_log(
    session: Session,
    task_id: int,
    device_id: int | None,
    level: str,
    step: str,
    message: str,
    context: dict | None = None,
) -> ExecutionLog:
    log = ExecutionLog(
        task_id=task_id,
        device_id=device_id,
        level=level,
        step=step,
        message=message,
        context_json=json.dumps(context or {}, ensure_ascii=False),
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def reclaim_expired_tasks(session: Session) -> int:
    """Fail any leased/running task whose lease expired and free its device.

    Triggered when an agent stops renewing (crash, network loss, app killed)
    for longer than ``lease_seconds`` — otherwise the task would hold the
    device forever and block the whole queue.
    """
    now = datetime.now(timezone.utc)
    statement = select(PublishTask).where(
        PublishTask.status.in_([TaskStatus.LEASED, TaskStatus.RUNNING]),
        PublishTask.lease_expires_at.is_not(None),
    )
    reclaimed = 0
    for task in session.exec(statement).all():
        expires_at = task.lease_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not expires_at or expires_at >= now:
            continue
        device_id = task.device_id
        task.status = TaskStatus.FAILED
        task.current_step = "lease_expired"
        task.error_message = "发到一半手机没了响应，这条已经自动停掉"
        task.finished_at = now
        task.updated_at = now
        task.lease_token = None
        task.lease_expires_at = None
        session.add(task)
        if device_id:
            device = session.get(Device, device_id)
            if device and device.current_task_id == task.id:
                device.status = DeviceStatus.ONLINE
                device.current_task_id = None
                device.updated_at = now
                session.add(device)
        session.commit()
        # 内容必须退回池 —— 这一行以前漏了（对比 fail_stuck_confirmations 里有），
        # 于是租约超时的任务占用的 ContentItem 永久停在 PUBLISHING：既不算已发布、
        # 也回不到待发，内容池被一条条慢慢吃空，而界面上只看到「发布中」数字在涨。
        _finalize_content(session, task, succeeded=False)
        try:
            notify_task_finished(session, task)
        except Exception:  # noqa: BLE001 — 通知永远不能影响业务
            pass
        append_log(
            session,
            task.id,
            device_id,
            "error",
            "lease_expired",
            "发到一半手机没了响应，这条已经自动停掉",
        )
        reclaimed += 1
    return reclaimed


# 任务在 running/leased 里**多久没有新日志**就算卡死。
# 正常发布每一步之间是秒级的（实测 +0/2/3/5/9/12/22 秒走完全程），
# 10 分钟一声不吭只有一种解释：它不动了。
STALLED_AFTER = timedelta(minutes=10)


def fail_stalled_tasks(session: Session) -> int:
    """任务卡在 running/leased、而且一直没有新日志 —— 收掉它，把手机让出来。

    ⚠ 这是一个**此前完全没人管**的状态。已有的两道闸都盖不到它：
    - `reclaim_expired_tasks` 看的是租约过期，而 Agent 还活着、一直在续租约；
    - `fail_stuck_confirmations` 只管 `waiting_confirmation`，而这条任务的
      DB 状态一直是 running（`waiting_since` 是 None）。

    2026-09-09 的现场：一条自动发布走到发布确认页、日志写着「内容已就绪，
    自动点击发布」，然后**整整 32 分钟一声不吭**，最后是被无障碍熔断顺手带走的 ——
    报出来的原因（「无障碍反复中断」）和真实情况完全不是一回事，
    而那台手机白占了半小时。

    判据用**最后一条日志的时刻**，不用 `updated_at`：后者会被续租约刷新，
    永远是"刚刚"，拿它判卡死等于永远判不出来。
    """
    now = datetime.now(timezone.utc)
    cutoff = now - STALLED_AFTER
    tasks = session.exec(
        select(PublishTask).where(
            PublishTask.status.in_([TaskStatus.RUNNING, TaskStatus.LEASED])
        )
    ).all()
    failed = 0
    for task in tasks:
        last_log = session.exec(
            select(ExecutionLog)
            .where(ExecutionLog.task_id == task.id)
            .order_by(ExecutionLog.created_at.desc())
        ).first()
        # 没有日志的用 started_at 兜底；两个都没有就跳过（还没真正开始）
        marker = last_log.created_at if last_log else task.started_at
        if not marker:
            continue
        if marker.tzinfo is None:
            marker = marker.replace(tzinfo=timezone.utc)
        if marker >= cutoff:
            continue
        stalled_min = int((now - marker).total_seconds() // 60)
        device_id = task.device_id
        # ⚠ 先把"卡在哪一步"读出来再改 current_step —— 顺序反了的话，
        # 这条消息里唯一有用的信息（卡在哪）会被自己刚赋的 "stalled" 覆盖掉。
        # 最后一条日志的 step 比 current_step 更准：它是 Agent 真正走到的地方。
        where = (last_log.step if last_log and last_log.step else task.current_step) or "执行中"
        task.status = TaskStatus.FAILED
        task.current_step = "stalled"
        task.error_message = (
            f"卡在「{where}」{stalled_min} 分钟没有任何动静，已自动结束并把手机让出来"
        )
        task.finished_at = now
        task.updated_at = now
        task.lease_token = None
        task.lease_expires_at = None
        session.add(task)
        if device_id:
            device = session.get(Device, device_id)
            if device and device.current_task_id == task.id:
                device.status = DeviceStatus.ONLINE
                device.current_task_id = None
                device.updated_at = now
                session.add(device)
        session.commit()
        _finalize_content(session, task, succeeded=False)
        try:
            notify_task_finished(session, task)
        except Exception:  # noqa: BLE001 — 通知永远不能影响业务
            pass
        append_log(
            session, task.id, device_id, "warning", "stalled",
            f"{stalled_min} 分钟没有新进展，这条自动结束，手机让给后面的任务",
        )
        failed += 1
    return failed


def fail_stuck_confirmations(session: Session, timeout_seconds: int) -> int:
    """Auto-fail tasks parked in waiting_confirmation longer than the timeout, so a
    page the agent couldn't handle doesn't hold the phone (and the whole queue,
    incl. 群发) forever. Runs independently of claiming since a busy device never
    claims. 0 disables."""
    if timeout_seconds <= 0:
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=timeout_seconds)
    tasks = session.exec(
        select(PublishTask).where(
            PublishTask.status == TaskStatus.WAITING_CONFIRMATION
        )
    ).all()
    failed = 0
    for task in tasks:
        # ⚠ 这里以前用 started_at（任务开跑的时刻），于是"执行时间 + 人工窗口"共用
        # 同一个预算：任务跑 9 分钟才转人工，人只剩 1 分钟；跑满预算才转人工，窗口
        # 直接是 0。线上 40 次超时里 38 次是在开跑后 600~660 秒被判死的，就是这么来的。
        # 改用 waiting_since = 真正转人工的时刻。老数据没有这个字段，退回 updated_at
        # （转人工时一定写过它），**绝不能退回 started_at**，否则老任务立刻被判死。
        waiting = task.waiting_since or task.updated_at
        if waiting and waiting.tzinfo is None:
            waiting = waiting.replace(tzinfo=timezone.utc)
        if waiting and waiting >= cutoff:
            continue  # still within the human-intervention window
        device_id = task.device_id
        task.status = TaskStatus.FAILED
        task.current_step = "confirmation_timeout"
        task.error_message = "卡在需要人工确认的页面，没人处理，已自动跳过"
        task.finished_at = now
        task.updated_at = now
        task.lease_token = None
        task.lease_expires_at = None
        session.add(task)
        if device_id:
            device = session.get(Device, device_id)
            if device and device.current_task_id == task.id:
                device.status = DeviceStatus.ONLINE
                device.current_task_id = None
                device.updated_at = now
                session.add(device)
        session.commit()
        # Return any pooled content to PENDING — otherwise a sweep-failed task
        # leaves its content item stuck in PUBLISHING forever (内容库「发布中」).
        _finalize_content(session, task, succeeded=False)
        try:
            notify_task_finished(session, task)
        except Exception:  # noqa: BLE001 — 通知永远不能影响业务
            pass
        append_log(
            session, task.id, device_id, "warning", "confirmation_timeout",
            "等人处理超时，这条自动跳过，后面的继续发",
        )
        failed += 1
    return failed


def claim_next_task(session: Session, device_id: int) -> PublishTask | None:
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    # ⚠ 无障碍没绑上的手机**不发任务**。它是这台手机做任何事的前提 ——
    # 服务没连上就读不到屏幕，任务领走也只能失败。
    #
    # 而失败是有代价的：终态失败会消耗当天的重试额度（FAIL_GIVE_UP=4），
    # 连挂四次这个号今天就不再尝试了。也就是说，一台无障碍掉了的手机会在
    # 几分钟内把自己当天的产能烧光 —— 等无障碍恢复时已经没有额度了。
    # 与其领走再失败，不如让任务留在队列里等它恢复。
    #
    # 只认**明确的 False**：None 表示这台手机还没上报过（老版本 Agent 或刚注册），
    # 那种情况按老行为放行，不能因为"不知道"就把整台设备停掉。
    #
    # 任务留在队列里不会被忘掉：超过 30 分钟没被领走、而设备又在线，
    # 「排队积压」告警会把它报出来（见 routes/alerts.py）。
    if device.accessibility_ok is False:
        return None

    reclaim_expired_tasks(session)

    # Concurrency: on Postgres we lock the candidate row with FOR UPDATE SKIP
    # LOCKED so multiple API workers never lease the same task (the correct
    # multi-worker primitive). On SQLite (single writer, single process) we fall
    # back to an in-process lock since it has no row-level locking.
    is_postgres = session.get_bind().dialect.name == "postgresql"
    guard = nullcontext() if is_postgres else _claim_lock

    with guard:
        # Auto-publish: top up the queue from the content pool for each platform
        # account on this phone (the phone runs one job at a time, but a device
        # can have douyin + xhs accounts each with its own quota).
        for account in session.exec(
            select(DeviceAccount).where(DeviceAccount.device_id == device_id)
        ).all():
            maybe_generate_auto_task(session, account)
        now = datetime.now(timezone.utc)
        statement = (
            select(PublishTask)
            .where(PublishTask.status == TaskStatus.QUEUED)
            .where(
                (PublishTask.scheduled_at.is_(None))
                | (PublishTask.scheduled_at <= now)
            )
            # Device-targeted only: a task is executed solely by its assigned
            # device (= one Douyin account). No random/any-device pickup.
            .where(PublishTask.target_device_id == device_id)
            .order_by(PublishTask.priority.desc(), PublishTask.created_at)
        )
        if is_postgres:
            statement = statement.with_for_update(skip_locked=True)
        task = session.exec(statement).first()
        if not task:
            return None

        task.status = TaskStatus.LEASED
        task.current_step = "claimed"
        task.progress = 2
        task.device_id = device_id
        task.lease_token = uuid4().hex
        task.lease_expires_at = now + timedelta(seconds=get_settings().lease_seconds)
        task.updated_at = now
        device.status = DeviceStatus.BUSY
        device.current_task_id = task.id
        device.updated_at = now
        session.add(task)
        session.add(device)
        session.commit()
        session.refresh(task)

    append_log(session, task.id, device_id, "info", "claimed", f"任务已被设备 {device.name} 领取")
    session.refresh(task)
    return task


def validate_lease(task: PublishTask, device_id: int, lease_token: str) -> None:
    if TaskStatus(task.status) in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="这条已经结束了")
    if task.device_id != device_id or task.lease_token != lease_token:
        raise HTTPException(status_code=409, detail="这条已经被别的手机接手了")
    expires_at = task.lease_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="这条已经超时了")


def renew_lease(
    session: Session,
    task: PublishTask,
    device_id: int,
    lease_token: str,
) -> PublishTask:
    validate_lease(task, device_id, lease_token)
    now = utcnow()
    task.lease_expires_at = now + timedelta(seconds=get_settings().lease_seconds)
    task.updated_at = now
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def update_agent_status(
    session: Session,
    task: PublishTask,
    payload: AgentStatusUpdate,
) -> PublishTask:
    validate_lease(task, payload.device_id, payload.lease_token)
    current = TaskStatus(task.status)
    target = TaskStatus(payload.status)
    if target != current and target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=f"不允许从 {current.value} 流转到 {target.value}",
        )

    now = utcnow()
    task.status = target
    task.current_step = payload.step
    task.progress = payload.progress
    task.error_message = payload.error_message
    task.updated_at = now
    if target == TaskStatus.RUNNING and not task.started_at:
        task.started_at = now
    if target == TaskStatus.WAITING_CONFIRMATION:
        # 人工窗口从"转人工的那一刻"开始计时（见 fail_stuck_confirmations）。
        # 同一个任务可能反复进出这个状态，每次都重新计时。
        task.waiting_since = now
    if target in TERMINAL_STATUSES:
        task.finished_at = now
        task.lease_expires_at = None
        device = session.get(Device, payload.device_id)
        if device:
            device.status = DeviceStatus.ONLINE
            device.current_task_id = None
            device.updated_at = now
            session.add(device)
    session.add(task)
    session.commit()
    session.refresh(task)
    # ⚠ 所有 notify.* 都必须在 commit **之后**调。
    # enqueue() 会另开一个 Session 去写 —— 这是为了不污染调用方的事务（见 notify.py
    # 顶部那段）。但如果调用方此刻还握着一个没提交的写事务，SQLite 的写锁就在本线程
    # 手上，内层那次 INSERT 只能等：等的正是自己，5 秒 busy_timeout 走完抛
    # "database is locked"，再被 enqueue 的兜底 except 悄悄吞掉。
    # 结果就是**告警静默丢失、不报错、不留痕**。转人工告警以前就写在 commit 之前，
    # 所以线上 40 次「超时未介入」里一条通知都没发出去过 —— 窗口修对了，人还是不来。
    if target == TaskStatus.WAITING_CONFIRMATION:
        try:
            _notify_waiting_for_human(session, task, payload)
        except Exception:  # noqa: BLE001 — 通知永远不能影响业务
            pass
    if target in TERMINAL_STATUSES:
        _finalize_content(session, task, succeeded=target == TaskStatus.SUCCEEDED)
        try:
            notify_task_finished(session, task)
        except Exception:  # noqa: BLE001 — 通知永远不能影响业务
            pass
    append_log(
        session,
        task.id,
        payload.device_id,
        "error" if target == TaskStatus.FAILED else "info",
        payload.step,
        payload.message or f"任务状态更新为 {target.value}",
    )
    session.refresh(task)
    return task


def _notify_waiting_for_human(
    session: Session, task: PublishTask, payload: AgentStatusUpdate
) -> None:
    """这一条卡住了、需要有人上手机 → 往群里说一句。

    S0 把人工窗口修对了（从卡住那一刻算 5 分钟），但线上 40 次超时全部是"没人来"——
    因为**根本没人看得见**。窗口修对了但没人来，窗口就没有意义。

    安全验证要立刻有人上手（critical，会 @值班），其余的等自动重试兜住就行。
    """
    from app.services import notify

    step = (payload.step or "").lower()
    critical = "security" in step or "验证" in (payload.message or "")
    city = ""
    account_id = None
    if task.device_id:
        account = session.exec(
            select(DeviceAccount)
            .where(DeviceAccount.device_id == task.device_id)
            .where(DeviceAccount.platform == task.platform)
        ).first()
        if account:
            city = notify.real_city(account.city)
            account_id = account.id
    device = session.get(Device, task.device_id) if task.device_id else None
    who = (device.douyin_nickname if device else None) or (device.name if device else "某个号")
    where = f"（{city}）" if city else ""
    machine = device.name if device else "这台手机"
    minutes = get_settings().confirmation_timeout_seconds // 60

    if critical:
        title = f"🚨 需要有人去手机上处理　{who}{where}"
        lines = [
            "抖音弹出了安全验证，自动操作已经停下",
            f"请到 {machine} 上把验证做掉，{minutes} 分钟内没人处理这条就自动跳过",
        ]
    else:
        title = f"⚠️ 这一条卡住了　{who}{where}"
        stuck = _STEP_CN.get(step, "")
        lines = [payload.message or "有个页面自动处理不了"]
        if stuck:
            lines.append(f"停在了{stuck}")
        lines.append(f"请到 {machine} 上看一下，{minutes} 分钟内没人处理就自动跳过")
    lines.append(f"编号 #{task.id}")

    notify.enqueue(
        event="human_security" if critical else "human_needed",
        # 同一条反复卡在同一个页面时不刷屏
        dedup_key=f"human:{task.id}:{step or 'unknown'}",
        title=title,
        severity="critical" if critical else "warning",
        city=city,
        device_id=task.device_id,
        account_id=account_id,
        task_id=task.id,
        payload={"lines": lines},
    )


def notify_task_finished(session: Session, task: PublishTask) -> None:
    """一条发完了（成功或失败）→ 往群里说一句。

    用户定的：**「我们目前抖音账号在用的本来就不多，我觉得就需要全部提醒，
    发布成功啥的」**。所以不聚合、不采样 —— 在用的号 ~20 个、每号每天 1 篇，
    一天二十来条，群里看得完；而"看得完"正是他们不用打开控制台的前提。
    号多起来了再改成聚合，别提前优化成看不见。

    调用方必须包在 try/except 里，而且**必须在 commit 之后调**（见 update_agent_status
    里那段注释：写在 commit 之前会被 SQLite 的写锁静默吃掉）。
    """
    from app.services import notify

    status = TaskStatus(task.status)
    if status == TaskStatus.CANCELLED:
        # 取消不是故障：要么是人自己在控制台点的（他知道），要么是被新内容顶掉的。
        # 播出去只会在群里制造一条"看着像失败、其实没事"的红色噪音。
        return
    device_id = task.device_id or task.target_device_id
    account = None
    if device_id:
        account = session.exec(
            select(DeviceAccount)
            .where(DeviceAccount.device_id == device_id)
            .where(DeviceAccount.platform == task.platform)
        ).first()
    device = session.get(Device, device_id) if device_id else None
    who = (account.nickname if account else "") or (device.name if device else "某个号")
    city = notify.real_city(account.city if account else "")
    where = f"（{city}）" if city else ""
    is_group = task.publish_type == "group_message"
    succeeded = status == TaskStatus.SUCCEEDED
    machine = device.name if device else ""
    tail = " · ".join(x for x in (machine, f"编号 #{task.id}") if x)

    lines = []
    if is_group:
        try:
            groups = json.loads(task.target_groups_json or "[]")
        except ValueError:
            groups = []
        target = "、".join(str(g) for g in groups[:3])[:60]
        if succeeded:
            title = f"✅ 群发成功　{who}{where}"
            lines.append(f"发到了 {target}" if target else "群消息已经发出去了")
        else:
            title = f"❌ 群发没成功　{who}{where}"
            lines.append(
                (task.error_message or "一个群都没发进去")[:120]
                + "，去手机上看一下截图"
            )
    elif succeeded:
        title = f"✅ 发布成功　{who}{where}"
        headline = (task.publish_title or task.cover_title or task.name or "")[:60]
        if headline:
            lines.append(f"发的是「{headline}」")
        lines.append(_quota_hint(session, account, device_id, task.platform))
    else:
        title = f"❌ 发布失败　{who}{where}"
        headline = (task.publish_title or task.cover_title or "")[:40]
        step = _STEP_CN.get((task.current_step or "").lower(), "")
        why = (task.error_message or "没记下失败的原因")[:120]
        prefix = f"「{headline}」" if headline else "这一条"
        lines.append(f"{prefix}{why}")
        if step:
            lines.append(f"停在了{step}")
        lines.append("去排期页重新排一条补上")
    lines.append(tail)

    notify.enqueue(
        event="publish_ok" if succeeded else "publish_failed",
        dedup_key=f"taskdone:{task.id}",   # 一条只播一次；重试是新的一条、新编号
        title=title,
        severity="info" if succeeded else "warning",
        city=city,
        device_id=device_id,
        account_id=account.id if account else None,
        task_id=task.id,
        payload={"lines": [x for x in lines if x]},
    )


# Agent 上报的是英文状态名，这里翻成**运营在手机屏幕上看得见的东西**。
# ⚠ 键必须和 Agent 实际发的字符串一一对得上。之前有 5 个键是凭印象写的，
# Agent 根本不发那些值，于是这行永远是空的 —— 一张对不上的映射表和没有映射
# 是一回事，而且不会报错。真实来源：android-agent 的 TaskCoordinator 里
# reporter.status(...) 的第二个参数（有用例盯着，见 test_copy_style / test_notify）。
AGENT_STEPS = {
    "claimed": "刚开始",
    "launching_douyin": "正在打开抖音",
    "opening_publish_entry": "正在点开发布",
    "selecting_publish_type": "正在选发布方式",
    "composing_text": "写文字那一页",
    "selecting_template": "选模板那一页",
    "selecting_media": "相册页（这条本来不该走相册）",
    "security_challenge": "抖音的安全验证",
    "unknown_page": "一个认不出来的页面",
    "publishing": "正在发布",
    "completed": "已完成",
}
BACKEND_STEPS = {
    "queued": "还没开始",
    "cancelled": "已取消",
    "lease_expired": "手机中途没了响应",
    "confirmation_timeout": "等人处理超时",
    "account_abnormal": "账号被限制",
}
# 「已完成」「还没开始」当"卡在哪"说了等于没说，不进消息
_MUTE_STEPS = {"completed", "claimed", "queued", "cancelled"}
_STEP_CN = {
    k: v for k, v in {**AGENT_STEPS, **BACKEND_STEPS}.items() if k not in _MUTE_STEPS
}


def _quota_hint(session: Session, account, device_id: int | None, platform: str) -> str:
    """成功消息里带一句"这个号今天发完没"，省得再去控制台数。"""
    if not device_id:
        return ""
    try:
        from app.services.notify_sweep import _expected_for, daily_target

        done = count_today_published(session, device_id, platform)
        want = _expected_for(account, daily_target(session)) if account else daily_target(session)
        if want and done >= want:
            return f"这个号今天的 {want} 篇发完了"
        return f"今天第 {done} 篇，还差 {max(want - done, 0)} 篇"
    except Exception:  # noqa: BLE001 — 附加信息而已，取不到就不带
        return ""


def append_agent_log(
    session: Session,
    task: PublishTask,
    payload: AgentLogCreate,
) -> ExecutionLog:
    validate_lease(task, payload.device_id, payload.lease_token)
    return append_log(
        session,
        task.id,
        payload.device_id,
        payload.level,
        payload.step,
        payload.message,
        payload.context,
    )
