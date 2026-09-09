"""点过发布按钮的任务失败之后，内容**不能**退回内容池。

2026-09-09 实测过一次代价：任务 #1672 在 17:10:42 点了发布，随即被换包
打断，任务记 `lease_expired`。内容退回池子，20 分钟后引擎自动补一条 ——
而手机上那条其实已经发出去了（当时已有 14 浏览）。差一点重复发到真实账号。

两边的代价不对称：重复发只能人工去删，少发一篇明天自动补得回来。
所以宁可当它发出去了。

而 0.9.0 那批如实的失败码（publish_button_missing / caption_empty /
declaration_blocked）都停在点击**之前** —— 它们必须照常退回池子，
否则每一次真失败都要人工救一篇内容出来。
"""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session

from app.core.enums import ContentStatus, DeviceStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from sqlmodel import select

from app.models.entities import ContentItem, Device, ExecutionLog, PublishTask
from app.services.tasks import _finalize_content, clicked_publish


def _setup(session: Session, name: str) -> tuple[Device, ContentItem]:
    # 全套测试共用一个库：城市必须唯一，否则别的用例的账号会把这里的内容
    # 从池子里捞走，串成互相看不懂的失败（见 test_fanout 的同款写法）。
    city = f"点发布城-{uuid.uuid4().hex[:6]}"
    device = Device(
        device_code=f"cp-{name}-{uuid.uuid4().hex[:8]}", name=name,
        status=DeviceStatus.ONLINE,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    content = ContentItem(
        cover_title=f"{name} 封面",
        title=f"{name} 标题",
        body="正文",
        city=city,
        platform="douyin",
        status=ContentStatus.PUBLISHING,
    )
    session.add(content)
    session.commit()
    session.refresh(content)
    return device, content


def _cleanup(session: Session, device: Device, content: ContentItem) -> None:
    """用完把行删干净 —— 全套测试共用一个库。

    留下的 pending 内容会被后面用例的账号从池子里捞走（`_pick_content` 的
    兜底分支不看城市），表现是别处冒出一条对不上号的任务、断言数量时挂掉，
    而错误信息里完全看不出跟这个文件有关。照 test_fanout 的 `_cleanup` 写。
    """
    for task in session.exec(
        select(PublishTask).where(PublishTask.content_id == content.id)
    ).all():
        for log in session.exec(
            select(ExecutionLog).where(ExecutionLog.task_id == task.id)
        ).all():
            session.delete(log)
        session.delete(task)
    session.delete(session.get(ContentItem, content.id))
    session.delete(session.get(Device, device.id))
    session.commit()


def _failed_task(session: Session, device: Device, content: ContentItem, step: str):
    task = PublishTask(
        name="post", platform="douyin", target_device_id=device.id,
        device_id=device.id, status=TaskStatus.FAILED,
        content_id=content.id, current_step=step,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def test_clicked_publish_keeps_content_out_of_the_pool():
    """点过发布 → 内容记「已发布」，不回池子，不会被再发一遍。"""
    create_db_and_tables()
    with Session(engine) as session:
        device, content = _setup(session, "clicked")
        try:
            task = _failed_task(session, device, content, "lease_expired")
            # Agent 在真正点下去之前发的最后一条状态
            session.add(ExecutionLog(
                task_id=task.id, device_id=device.id, level="info",
                step="publishing", message="内容已就绪，自动点击发布",
            ))
            session.commit()

            assert clicked_publish(session, task) is True
            _finalize_content(session, task, succeeded=False)
            session.refresh(content)
            assert content.status == ContentStatus.PUBLISHED
            assert content.published_task_id == task.id
        finally:
            _cleanup(session, device, content)


def test_never_clicked_returns_to_the_pool():
    """按钮就没找到 → 手机上一定没有这条，照常退回池子等下一个时段。"""
    create_db_and_tables()
    with Session(engine) as session:
        device, content = _setup(session, "notclicked")
        try:
            task = _failed_task(session, device, content, "publish_button_missing")
            # 一路上只有 debug 插桩和更早的步骤，没有 info 级的 publishing
            session.add(ExecutionLog(
                task_id=task.id, device_id=device.id, level="debug",
                step="publishing", message="点发布：第 3 次结果=false",
            ))
            session.add(ExecutionLog(
                task_id=task.id, device_id=device.id, level="info",
                step="composing_text", message="已输入文字内容，进入下一步",
            ))
            session.commit()

            assert clicked_publish(session, task) is False
            _finalize_content(session, task, succeeded=False)
            session.refresh(content)
            assert content.status == ContentStatus.PENDING
            assert content.published_task_id is None
        finally:
            _cleanup(session, device, content)


def test_debug_instrumentation_alone_does_not_count_as_clicked():
    """排查用的 debug 日志不能把内容锁死 —— 那会让每次真失败都要人工救。"""
    create_db_and_tables()
    with Session(engine) as session:
        device, content = _setup(session, "debugonly")
        try:
            task = _failed_task(session, device, content, "caption_empty")
            for i in range(5):
                session.add(ExecutionLog(
                    task_id=task.id, device_id=device.id, level="debug",
                    step="verifying", message=f"结果确认第 {i} 轮",
                ))
            session.add(ExecutionLog(
                task_id=task.id, device_id=device.id, level="warning",
                step="publishing", message="标题没能写进去，正文正常，继续发布",
            ))
            session.commit()

            assert clicked_publish(session, task) is False
            _finalize_content(session, task, succeeded=False)
            session.refresh(content)
            assert content.status == ContentStatus.PENDING
        finally:
            _cleanup(session, device, content)
