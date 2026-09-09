"""S0 止血：三个会持续吃掉产能和内容池的线上缺陷。

每个用例都对应一条线上实测：
- 人工窗口从 started_at 起算 → 40 次超时里 38 次在开跑后 600~660 秒被判死
- 租约超时不退还内容 → ContentItem 永久卡 PUBLISHING
- 未到点的排期被算成"正在进行" → 排一条明天的就掐死今天的自动发布
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.core.enums import ContentStatus, DeviceStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import ContentItem, Device, DeviceAccount, PublishTask
from app.services.tasks import (
    claim_next_task,
    fail_stuck_confirmations,
    maybe_generate_auto_task,
    reclaim_expired_tasks,
)


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _device(session: Session, code: str, name: str, status) -> Device:
    """套件共用一个 sqlite 文件，device_code 有唯一约束 —— 取不到才建，
    避免单跑过一次之后再跑全量就撞唯一键。"""
    from sqlmodel import select as _select

    found = session.exec(_select(Device).where(Device.device_code == code)).first()
    if found:
        found.status = status
        found.current_task_id = None
        session.add(found)
        session.commit()
        session.refresh(found)
        return found
    device = Device(device_code=code, name=name, status=status)
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def test_human_window_counts_from_waiting_since_not_task_start():
    """一个跑了很久才转人工的任务，人工窗口要从『转人工』算，不是从『开跑』算。"""
    create_db_and_tables()
    with Session(engine) as session:
        device = _device(session, "win-dev", "win", DeviceStatus.BUSY)

        now = datetime.now(timezone.utc)
        # 开跑于 30 分钟前（早已超出 300 秒预算），但 1 分钟前才转人工。
        task = PublishTask(
            name="slow-then-waiting", platform="douyin",
            target_device_id=device.id, device_id=device.id,
            status=TaskStatus.WAITING_CONFIRMATION,
            started_at=_naive(now - timedelta(minutes=30)),
            waiting_since=_naive(now - timedelta(minutes=1)),
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        failed = fail_stuck_confirmations(session, 300)
        session.refresh(task)
        assert failed == 0, "刚转人工 1 分钟就被判死 —— 人工窗口还在按 started_at 算"
        assert task.status == TaskStatus.WAITING_CONFIRMATION

        # 把转人工时刻推到 10 分钟前 → 这次才该判死。
        task.waiting_since = _naive(now - timedelta(minutes=10))
        session.add(task)
        session.commit()
        assert fail_stuck_confirmations(session, 300) == 1
        session.refresh(task)
        assert task.status == TaskStatus.FAILED
        assert task.current_step == "confirmation_timeout"


def test_lease_expiry_returns_content_to_the_pool():
    """租约超时判失败时，占用的内容必须退回待发，否则永久卡在『发布中』。"""
    create_db_and_tables()
    with Session(engine) as session:
        device = _device(session, "lease-dev", "lease", DeviceStatus.BUSY)
        content = ContentItem(
            title="被租约超时卡住的内容", body="正文",
            status=ContentStatus.PUBLISHING, platform="douyin",
        )
        session.add(content)
        session.commit()
        session.refresh(content)

        now = datetime.now(timezone.utc)
        task = PublishTask(
            name="lease-expired", platform="douyin",
            target_device_id=device.id, device_id=device.id,
            content_id=content.id,
            status=TaskStatus.RUNNING,
            lease_token="t", lease_expires_at=_naive(now - timedelta(minutes=5)),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        device.current_task_id = task.id
        session.add(device)
        session.commit()

        assert reclaim_expired_tasks(session) == 1
        session.refresh(task)
        session.refresh(content)
        session.refresh(device)
        assert task.status == TaskStatus.FAILED
        assert content.status == ContentStatus.PENDING, "内容没退回池，会永久卡在发布中"
        assert content.published_task_id is None
        assert device.current_task_id is None


def test_future_schedule_does_not_block_todays_auto_publish():
    """给某台机排一条明天的内容，不能把它今天的自动发布掐死。"""
    create_db_and_tables()
    with Session(engine) as session:
        device = _device(session, "sched-dev", "sched", DeviceStatus.ONLINE)
        account = DeviceAccount(
            device_id=device.id, platform="douyin", nickname="排期测试号",
            city="排期隔离城", auto_publish=True, daily_quota=2, health="normal",
        )
        pooled = ContentItem(
            title="今天该发的内容", body="正文",
            status=ContentStatus.PENDING, platform="douyin", city="排期隔离城",
        )
        session.add(account)
        session.add(pooled)
        session.commit()
        session.refresh(account)
        session.refresh(pooled)

        # 一条排在明天的 queued 任务 —— 它不该算作"这台机正在忙"。
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        scheduled = PublishTask(
            name="明天 09:00 的排期", platform="douyin",
            target_device_id=device.id, status=TaskStatus.QUEUED,
            scheduled_at=_naive(tomorrow),
        )
        session.add(scheduled)
        session.commit()

        maybe_generate_auto_task(session, account)
        session.refresh(pooled)
        assert pooled.status == ContentStatus.PUBLISHING, (
            "未到点的排期把今天的自动发布掐死了 —— 并发闸还在把它算作正在进行"
        )


def test_自动发布的每日篇数和首页通知同一个口径():
    """引擎不能自己算一份「每天几篇」。

    通知和首页用的是 `_expected_for` = **min(全局目标, 该号上限)**；
    引擎这一行以前直接用 `account.daily_quota`，没取 min。线上 daily_quota=2、
    全局目标=1，一旦打开自动发布就是引擎按 2 篇发、首页按 1 篇算目标，
    第一天显示「今日已发 28 / 14」——又一次"同一件事两个口径"。
    因为自动发布从来没在生产上开过，这个洞一直没暴露。
    """
    from app.services import settings_store
    from app.services.notify_sweep import DAILY_TARGET_KEY
    from app.services.tasks import cn_day_start_utc

    create_db_and_tables()
    with Session(engine) as session:
        device = _device(session, "quota-dev", "quota", DeviceStatus.ONLINE)
        for row in session.exec(
            __import__("sqlmodel").select(DeviceAccount).where(
                DeviceAccount.device_id == device.id
            )
        ).all():
            session.delete(row)
        session.commit()
        account = DeviceAccount(
            device_id=device.id, platform="douyin", nickname="配额号",
            city="通用", auto_publish=True, daily_quota=2, health="normal",
        )
        session.add(account)
        # 池子里备两条，够发第二条（如果闸放行的话）
        for i in range(2):
            session.add(ContentItem(
                title=f"配额测试{i}", body="正文正文", city="通用",
                platform="douyin", status=ContentStatus.PENDING,
            ))
        # 今天已经成功发过一条，且远早于最小间隔（3 小时），把别的闸都让开
        session.add(PublishTask(
            name="今天已发的那条", platform="douyin",
            target_device_id=device.id, device_id=device.id,
            publish_type="text", status=TaskStatus.SUCCEEDED,
            finished_at=cn_day_start_utc() + timedelta(minutes=5),
        ))
        session.commit()
        session.refresh(account)

        def _queued() -> int:
            from sqlmodel import func, select as _sel
            return session.exec(
                _sel(func.count()).select_from(PublishTask)
                .where(PublishTask.target_device_id == device.id)
                .where(PublishTask.status == TaskStatus.QUEUED)
            ).one()

        # 全局目标 = 1，单号上限 = 2 → 实际期望 1 篇，今天已经发过 → 不该再排
        settings_store.put(session, DAILY_TARGET_KEY, "1")
        maybe_generate_auto_task(session, account)
        assert _queued() == 0, (
            "引擎按单号上限 2 篇发了第二条，而首页和企微群按全局目标 1 篇算 —— 两个口径又分叉了"
        )

        # 把全局目标提到 2 → min(2, 2) = 2，这时才该排第二条
        settings_store.put(session, DAILY_TARGET_KEY, "2")
        maybe_generate_auto_task(session, account)
        assert _queued() == 1, "全局目标提到 2 之后应该允许发第二条"

        settings_store.put(session, DAILY_TARGET_KEY, "1")


def _pub_account(session: Session, code: str) -> "DeviceAccount":
    from sqlmodel import select as _sel

    device = _device(session, code, code, DeviceStatus.ONLINE)
    # ⚠ 套件共用一个 sqlite 文件，_device 也是"取不到才建"。**任务也要清**，
    # 否则上一轮留下的任务会被算进 made_today，第二次跑就崩（踩过）。
    for row in session.exec(
        _sel(PublishTask).where(PublishTask.target_device_id == device.id)
    ).all():
        session.delete(row)
    for row in session.exec(
        _sel(DeviceAccount).where(DeviceAccount.device_id == device.id)
    ).all():
        session.delete(row)
    session.commit()
    account = DeviceAccount(
        device_id=device.id, platform="douyin", nickname=code,
        city="通用", auto_publish=True, daily_quota=3, health="normal",
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _task(session: Session, account, status, finished_offset_min: int) -> None:
    from app.services.tasks import cn_day_start_utc

    session.add(PublishTask(
        name="auto", platform="douyin", publish_type="text",
        target_device_id=account.device_id, device_id=account.device_id,
        status=status,
        created_at=cn_day_start_utc() + timedelta(hours=1),
        finished_at=datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(minutes=finished_offset_min),
    ))
    session.commit()


def test_失败的发布不占今天的名额_后面的时段会补回来():
    """10:30 那条失败了，它以前照样占着今天的一个名额 —— 那一篇永远补不回来，
    当天就是少发一篇。运营只看到"今天差一篇"，不知道是哪条、为什么。

    失败必须把名额吐出来，后面的时间段才会把它补上。这才是闭环。
    """
    from app.services import settings_store
    from app.services.notify_sweep import DAILY_TARGET_KEY
    from app.services.schedule_windows import CN_TZ
    from app.services.tasks import PUBLISH_WINDOWS_KEY, _due_by_plan

    create_db_and_tables()
    with Session(engine) as session:
        account = _pub_account(session, "retry-dev")
        settings_store.put(session, PUBLISH_WINDOWS_KEY, "10-11,14-15,17-18")
        settings_store.put(session, DAILY_TARGET_KEY, "3")

        # 中国时间 15:00 —— 10 点和 14 点那两个时刻都过了，今天该发 2 条
        now = datetime(2026, 9, 8, 15, 0, tzinfo=CN_TZ).astimezone(timezone.utc)

        _task(session, account, TaskStatus.SUCCEEDED, 240)   # 10 点那条发成功了
        _task(session, account, TaskStatus.FAILED, 30)       # 14 点那条失败了
        assert _due_by_plan(session, account, now) is True, (
            "失败的那条还占着名额 —— 今天少发的这一篇永远补不回来"
        )

        # 两条都成功，才算今天这两个点都用掉了
        for row in session.exec(
            __import__("sqlmodel").select(PublishTask).where(
                PublishTask.status == TaskStatus.FAILED
            )
        ).all():
            row.status = TaskStatus.SUCCEEDED
            session.add(row)
        session.commit()
        assert _due_by_plan(session, account, now) is False
        settings_store.put(session, PUBLISH_WINDOWS_KEY, "")


def test_失败之后要退避_否则坏手机会吃光并发名额():
    """「失败不占名额」如果不配上退避，就变成「一台坏手机在同一个时段里反复重试」：
    每个 4 秒的 tick 都重派一条、每条又失败。全矩阵只有 3 个并发名额，
    3 台这样的手机就能把所有号的产能压到零，而且控制台上全绿。
    """
    from app.services.tasks import FAIL_GIVE_UP, _failure_backoff

    create_db_and_tables()
    with Session(engine) as session:
        account = _pub_account(session, "backoff-dev")
        now = datetime.now(timezone.utc)

        assert _failure_backoff(session, account, now) is False, "没失败过就不该等"

        _task(session, account, TaskStatus.FAILED, 5)        # 5 分钟前刚失败
        assert _failure_backoff(session, account, now) is True, "刚失败完就重试了"

        # 把它挪到 25 分钟前 —— 超过第一次的 20 分钟退避，可以再试
        row = session.exec(
            __import__("sqlmodel").select(PublishTask).where(
                PublishTask.target_device_id == account.device_id
            )
        ).first()
        row.finished_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=25
        )
        session.add(row)
        session.commit()
        assert _failure_backoff(session, account, now) is False, "退避时间到了还不放行"

        # 连续失败到上限 → 当天不再试
        for _ in range(FAIL_GIVE_UP):
            _task(session, account, TaskStatus.FAILED, 200)
        assert _failure_backoff(session, account, now) is True, (
            f"连续失败 {FAIL_GIVE_UP} 次还在重试 —— 再试也是白试，还占着并发名额"
        )

        # 中间有一次成功就该重新计数
        _task(session, account, TaskStatus.SUCCEEDED, 1)
        assert _failure_backoff(session, account, now) is False, (
            "有过一次成功之后，连续失败应该从头数"
        )


def test_无障碍没绑上的手机不发任务():
    """无障碍是这台手机做任何事的前提 —— 没连上就读不到屏幕，
    任务领走也只能失败。

    而失败是有代价的：终态失败消耗当天的重试额度（FAIL_GIVE_UP=4），
    连挂四次这个号今天就不再尝试了。一台无障碍掉了的手机会在几分钟内
    把自己当天的产能烧光，等它恢复时已经没有额度了。

    只认明确的 False —— None 是「还没上报过」（老版本 Agent / 刚注册），
    不能因为"不知道"就把整台设备停掉。
    """
    create_db_and_tables()
    with Session(engine) as session:
        device = Device(
            device_code=f"a11y-claim-{uuid.uuid4().hex[:8]}",
            name="无障碍测试机",
            status=DeviceStatus.ONLINE,
            last_heartbeat_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        task = PublishTask(
            name="等着被领的任务", platform="douyin", publish_type="text",
            target_device_id=device.id, status=TaskStatus.QUEUED,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        dev_id, task_id = device.id, task.id

        try:
            # ① 无障碍没绑上 → 一条都不发，任务留在队列里
            device.accessibility_ok = False
            session.add(device)
            session.commit()
            assert claim_next_task(session, dev_id) is None
            assert session.get(PublishTask, task_id).status == TaskStatus.QUEUED

            # ② 还没上报过（None）→ 按老行为放行，不能因为"不知道"就停掉整台设备
            device.accessibility_ok = None
            session.add(device)
            session.commit()
            got = claim_next_task(session, dev_id)
            assert got is not None and got.id == task_id

            # 放回队列，再验恢复后能领
            got.status = TaskStatus.QUEUED
            got.device_id = None
            got.lease_token = None
            got.lease_expires_at = None
            device.status = DeviceStatus.ONLINE
            device.current_task_id = None
            device.accessibility_ok = True
            session.add(got)
            session.add(device)
            session.commit()

            # ③ 恢复之后照常领
            again = claim_next_task(session, dev_id)
            assert again is not None and again.id == task_id
        finally:
            with Session(engine) as s2:
                t = s2.get(PublishTask, task_id)
                if t:
                    s2.delete(t)
                d = s2.get(Device, dev_id)
                if d:
                    s2.delete(d)
                s2.commit()
