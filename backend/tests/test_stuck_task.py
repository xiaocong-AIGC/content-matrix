"""A task stuck in waiting_confirmation must be auto-failed past the timeout so it
stops holding the one-job-per-phone device (which would block 群发 etc.).

窗口从 `waiting_since`（转人工那一刻）算起，不是从 `started_at`（任务开跑）算 ——
后者会让"执行时间"吃掉"留给人的时间"，见 test_s0_bleeding。"""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.enums import DeviceStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import Device, PublishTask
from app.services.tasks import fail_stuck_confirmations


def test_stuck_confirmation_is_auto_failed_and_device_freed():
    create_db_and_tables()
    with Session(engine) as session:
        device = Device(device_code="stuck-dev", name="stuck", status=DeviceStatus.BUSY)
        session.add(device)
        session.commit()
        session.refresh(device)

        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        recent = datetime.now(timezone.utc) - timedelta(minutes=2)
        stuck = PublishTask(
            name="stuck-post", platform="xhs", target_device_id=device.id,
            device_id=device.id, status=TaskStatus.WAITING_CONFIRMATION,
            started_at=old, waiting_since=old,
        )
        fresh = PublishTask(
            name="fresh-post", platform="xhs", target_device_id=device.id,
            device_id=device.id, status=TaskStatus.WAITING_CONFIRMATION,
            started_at=recent, waiting_since=recent,
        )
        session.add(stuck)
        session.add(fresh)
        session.commit()
        session.refresh(stuck)
        session.refresh(fresh)
        device.current_task_id = stuck.id
        session.add(device)
        session.commit()

        # 10-min timeout: the 20-min-old one fails, the 2-min-old one survives.
        n = fail_stuck_confirmations(session, timeout_seconds=600)
        assert n == 1

        session.refresh(stuck)
        session.refresh(fresh)
        session.refresh(device)
        assert stuck.status == TaskStatus.FAILED
        assert stuck.current_step == "confirmation_timeout"
        assert fresh.status == TaskStatus.WAITING_CONFIRMATION  # still within window
        assert device.current_task_id is None
        assert device.status == DeviceStatus.ONLINE

        # disabled (0) is a no-op
        assert fail_stuck_confirmations(session, timeout_seconds=0) == 0


def test_卡在running的任务要被收掉():
    """2026-09-09 的现场：一条自动发布走到发布确认页、日志写着「内容已就绪，
    自动点击发布」，然后**整整 32 分钟一声不吭**，最后是被无障碍熔断顺手带走的 ——
    报出来的原因和真实情况完全不是一回事，那台手机白占了半小时。

    此前两道闸都盖不到它：租约回收看的是租约过期，而 Agent 还活着一直在续；
    人工确认超时只管 waiting_confirmation，而它的状态一直是 running。

    判据必须用**最后一条日志的时刻**，不能用 updated_at —— 后者被续租约刷新，
    永远是"刚刚"，拿它判卡死等于永远判不出来。
    """
    import uuid as _uuid

    from app.models.entities import ExecutionLog
    from app.services.tasks import fail_stalled_tasks

    create_db_and_tables()
    with Session(engine) as session:
        dev = Device(
            device_code=f"stall-{_uuid.uuid4().hex[:8]}", name="卡死机",
            status=DeviceStatus.ONLINE,
            last_heartbeat_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(dev)
        session.commit()
        session.refresh(dev)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        made = []
        for name, ago_min in (("卡了半小时的", 32), ("刚跑起来的", 2)):
            t = PublishTask(
                name=name, platform="douyin", publish_type="text",
                target_device_id=dev.id, device_id=dev.id,
                status=TaskStatus.RUNNING,
                started_at=now - timedelta(minutes=ago_min),
                # ⚠ updated_at 是"刚刚"——模拟 Agent 一直在续租约
                updated_at=now,
                lease_expires_at=now + timedelta(minutes=5),
            )
            session.add(t)
            session.commit()
            session.refresh(t)
            session.add(ExecutionLog(
                task_id=t.id, device_id=dev.id, level="info", step="publishing",
                message="内容已就绪，自动点击发布",
                created_at=now - timedelta(minutes=ago_min),
            ))
            session.commit()
            made.append(t.id)
        # 无障碍连接/断开的日志不该算进展 —— 给「卡了半小时的」补一条刚刚的，
        # 它仍然必须被判定为卡死
        session.add(ExecutionLog(
            task_id=made[0], device_id=dev.id, level="info",
            step="accessibility_ready", message="无障碍服务已连接",
            created_at=now,
        ))
        session.commit()
        dev.current_task_id = made[0]
        session.add(dev)
        session.commit()
        dev_id = dev.id

        try:
            assert fail_stalled_tasks(session) == 1, "只该收掉卡了 32 分钟的那条"
            with Session(engine) as s2:
                dead = s2.get(PublishTask, made[0])
                alive = s2.get(PublishTask, made[1])
                assert dead.status == TaskStatus.FAILED
                assert "没有任何动静" in (dead.error_message or "")
                assert "32 分钟" in (dead.error_message or "")
                # 必须说清卡在哪一步 —— 那是这条消息唯一有用的信息
                assert "publishing" in (dead.error_message or ""), dead.error_message
                # 手机必须被让出来，否则整条队列（含群发）继续堵着
                d = s2.get(Device, dev_id)
                assert d.current_task_id is None and d.status == DeviceStatus.ONLINE
                # 刚跑起来的那条不能动
                assert alive.status == TaskStatus.RUNNING
        finally:
            with Session(engine) as s2:
                for tid in made:
                    for lg in s2.exec(
                        select(ExecutionLog).where(ExecutionLog.task_id == tid)
                    ).all():
                        s2.delete(lg)
                    t = s2.get(PublishTask, tid)
                    if t:
                        s2.delete(t)
                d = s2.get(Device, dev_id)
                if d:
                    s2.delete(d)
                s2.commit()
