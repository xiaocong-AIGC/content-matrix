"""Phase C — `both` content fans out to 抖音 + 小红书, each independent."""
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.db.session import engine
from app.core.enums import ContentStatus, TaskStatus
from app.models.entities import ContentItem, Device, DeviceAccount, PublishTask
from app.services.tasks import _finalize_content, consume_content_into_task


import uuid


def _setup(session: Session):
    # Unique city so these fixtures never match another (shared-DB) test's account.
    city = f"扇出城-{uuid.uuid4().hex[:6]}"
    dev = Device(device_code=f"fanout-{uuid.uuid4().hex[:8]}", name="扇出机")
    session.add(dev)
    session.commit()
    session.refresh(dev)
    dy = DeviceAccount(device_id=dev.id, platform="douyin", city=city, health="normal")
    xhs = DeviceAccount(device_id=dev.id, platform="xhs", city=city, health="normal")
    content = ContentItem(
        body="跨平台内容", cover_title="跨平台", platform="both", city=city,
        status=ContentStatus.PENDING, topics_json="[]",
    )
    session.add_all([dy, xhs, content])
    session.commit()
    for o in (dy, xhs, content):
        session.refresh(o)
    return dev, dy, xhs, content


def _tasks_for(session, content_id):
    return session.exec(
        select(PublishTask).where(PublishTask.content_id == content_id)
    ).all()


def _cleanup(session, dev, content):
    """Remove created rows — the suite shares one DB, so leftover tasks/content
    would pollute other tests' global queries."""
    for t in _tasks_for(session, content.id):
        session.delete(t)
    session.delete(session.get(ContentItem, content.id))
    for a in session.exec(
        select(DeviceAccount).where(DeviceAccount.device_id == dev.id)
    ).all():
        session.delete(a)
    session.delete(session.get(Device, dev.id))
    session.commit()


def test_both_content_fans_out_to_two_platforms():
    with TestClient(app):
        with Session(engine) as session:
            dev, dy, xhs, content = _setup(session)
            try:
                task = consume_content_into_task(session, dy)
                assert task is not None
                tasks = _tasks_for(session, content.id)
                assert sorted(t.platform for t in tasks) == ["douyin", "xhs"]
                session.refresh(content)
                assert content.status == ContentStatus.PUBLISHING  # both still pending
            finally:
                _cleanup(session, dev, content)


def test_both_published_when_one_platform_succeeds():
    with TestClient(app):
        with Session(engine) as session:
            dev, dy, xhs, content = _setup(session)
            try:
                consume_content_into_task(session, dy)
                tasks = _tasks_for(session, content.id)
                dy_task = next(t for t in tasks if t.platform == "douyin")
                xhs_task = next(t for t in tasks if t.platform == "xhs")
                # 抖音 succeeds, 小红书 fails → PUBLISHED (live on one platform).
                dy_task.status = TaskStatus.SUCCEEDED
                xhs_task.status = TaskStatus.FAILED
                session.add_all([dy_task, xhs_task])
                session.commit()
                _finalize_content(session, xhs_task, succeeded=False)
                session.refresh(content)
                assert content.status == ContentStatus.PUBLISHED
                assert content.published_task_id == dy_task.id  # attributed to winner
            finally:
                _cleanup(session, dev, content)


def test_both_returns_to_pool_only_when_all_fail():
    with TestClient(app):
        with Session(engine) as session:
            dev, dy, xhs, content = _setup(session)
            try:
                consume_content_into_task(session, dy)
                tasks = _tasks_for(session, content.id)
                for t in tasks:
                    t.status = TaskStatus.FAILED
                    session.add(t)
                session.commit()
                _finalize_content(session, tasks[0], succeeded=False)
                session.refresh(content)
                assert content.status == ContentStatus.PENDING  # back to the pool
            finally:
                _cleanup(session, dev, content)
