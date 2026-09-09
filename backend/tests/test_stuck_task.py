"""A task stuck in waiting_confirmation must be auto-failed past the timeout so it
stops holding the one-job-per-phone device (which would block 群发 etc.).

窗口从 `waiting_since`（转人工那一刻）算起，不是从 `started_at`（任务开跑）算 ——
后者会让"执行时间"吃掉"留给人的时间"，见 test_s0_bleeding。"""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

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
