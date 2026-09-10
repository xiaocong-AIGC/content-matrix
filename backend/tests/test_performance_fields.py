"""效果榜要能回答「这条是谁发的、哪天发的、发了几天了」。

榜上只有标题和数字的时候，运营没法判断「这条数字低是内容不行，还是昨天刚发」，
也没法把一条爆款归到某个号头上 —— 只能一条条点进去看。
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.enums import ContentStatus, DeviceStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import (
    ContentItem,
    Device,
    DeviceAccount,
    PostMetric,
    PublishTask,
)
from app.services.metrics import content_performance


def test_board_shows_who_published_it_and_when():
    create_db_and_tables()
    with Session(engine) as session:
        tag = uuid.uuid4().hex[:8]
        city = f"效果榜城-{tag}"
        device = Device(
            device_code=f"perf-{tag}", name=f"云机{tag}", status=DeviceStatus.ONLINE
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        account = DeviceAccount(
            device_id=device.id, platform="douyin",
            nickname=f"测试号{tag}", city=city, health="normal",
        )
        content = ContentItem(
            cover_title="封面", title="标题", body="正文",
            city=city, platform="douyin", status=ContentStatus.PUBLISHED,
        )
        session.add_all([account, content])
        session.commit()
        session.refresh(content)

        published = datetime.now(timezone.utc) - timedelta(days=3)
        task = PublishTask(
            name="post", platform="douyin", target_device_id=device.id,
            device_id=device.id, status=TaskStatus.SUCCEEDED,
            content_id=content.id, finished_at=published,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        session.add(PostMetric(
            task_id=task.id, content_id=content.id, device_id=device.id,
            platform="douyin", title="标题", body="正文",
            views=5000, likes=40, collects=6, comments=88,
        ))
        session.commit()

        try:
            rows = content_performance(session, limit=2000)
            row = next(r for r in rows if r["content_id"] == content.id)
            assert row["device_name"] == f"云机{tag}"
            assert row["account_nickname"] == f"测试号{tag}"
            assert row["city"] == city
            assert row["published_at"] is not None
            # 发了 3 天 —— 边界上可能被算成 2 或 3，别把测试写成秒级精确
            assert row["age_days"] in (2, 3)
            # 主指标是评论（拍板项 2），确认它在返回里
            assert row["comments"] == 88
        finally:
            for m in session.exec(
                select(PostMetric).where(PostMetric.content_id == content.id)
            ).all():
                session.delete(m)
            session.delete(session.get(PublishTask, task.id))
            session.delete(session.get(ContentItem, content.id))
            session.delete(session.get(DeviceAccount, account.id))
            session.delete(session.get(Device, device.id))
            session.commit()


def test_board_survives_a_content_with_no_task():
    """回采到的笔记不一定对得上我们的发布任务（匹配是模糊的）。

    对不上的时候这几个字段应该是 None，而不是把整个榜单打挂 —— 榜上
    447/638 的行本来就没有对应的 ContentItem。
    """
    create_db_and_tables()
    with Session(engine) as session:
        tag = uuid.uuid4().hex[:8]
        content = ContentItem(
            cover_title="孤儿封面", title="孤儿标题", body="正文",
            city=f"孤儿城-{tag}", platform="douyin", status=ContentStatus.PUBLISHED,
        )
        session.add(content)
        session.commit()
        session.refresh(content)
        session.add(PostMetric(
            content_id=content.id, platform="douyin",
            title="孤儿标题", body="正文", views=100, comments=1,
        ))
        session.commit()
        try:
            rows = content_performance(session, limit=2000)
            row = next(r for r in rows if r["content_id"] == content.id)
            assert row["device_name"] is None
            assert row["account_nickname"] is None
            assert row["published_at"] is None
            assert row["age_days"] is None
        finally:
            for m in session.exec(
                select(PostMetric).where(PostMetric.content_id == content.id)
            ).all():
                session.delete(m)
            session.delete(session.get(ContentItem, content.id))
            session.commit()
