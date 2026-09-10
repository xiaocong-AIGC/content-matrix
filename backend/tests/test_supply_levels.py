"""自动生成页要能看见**够用的城市**的库存，不能只显示有缺口的。

2026-09-10 运营反馈「怎么都没数据啊，深圳不是在发布呢」：全矩阵唯一真正在跑的
城市（深圳，仅有的 5 个自动发布账号都在那）整行都是破折号 —— 因为 `city_gaps`
只返回有缺口的城市，而深圳一直「够用」。库存正好压在 45 篇 = 需要 45 篇的线上，
页面上一个数字也看不到。等它掉下去才第一次显示数字，那时已经晚了。
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.enums import ContentStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import ContentItem, Device, DeviceAccount, PublishTask
from app.services.content_supply import city_gaps


def _running_city(session: Session, city: str, stock: int):
    """一个「有号在跑」的城市：账号 + 一条 2 小时前发成功的任务 + N 篇待发库存。

    「在跑」的判据是最近 48 小时发过（和通知那边同口径），不是账号存在。
    """
    tag = uuid.uuid4().hex[:8]
    dev = Device(device_code=f"sup-{tag}", name=f"云机{tag}")
    session.add(dev)
    session.commit()
    session.refresh(dev)
    acc = DeviceAccount(
        device_id=dev.id, platform="douyin", nickname=f"号{tag}",
        city=city, health="normal", auto_publish=True,
    )
    task = PublishTask(
        name="近期发布", platform="douyin", target_device_id=dev.id,
        device_id=dev.id, status=TaskStatus.SUCCEEDED,
        finished_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session.add_all([acc, task])
    items = [
        ContentItem(
            cover_title=f"{city}待发{i}", title=f"{city}{i}", body="正文",
            city=city, platform="douyin", status=ContentStatus.PENDING,
        )
        for i in range(stock)
    ]
    session.add_all(items)
    session.commit()
    return dev, acc, task, items


def _cleanup(session: Session, dev, acc, task, items):
    for it in items:
        session.delete(session.get(ContentItem, it.id))
    session.delete(session.get(PublishTask, task.id))
    session.delete(session.get(DeviceAccount, acc.id))
    session.delete(session.get(Device, dev.id))
    session.commit()


def test_a_city_with_enough_stock_is_hidden_by_default_but_shown_on_demand():
    create_db_and_tables()
    with Session(engine) as session:
        city = f"够用城-{uuid.uuid4().hex[:6]}"
        made = _running_city(session, city, stock=30)   # 远超需要
        try:
            # 生成侧：够用的城市不该出现，否则会为它白生成一批
            lean = {g["city"] for g in city_gaps(session)}
            assert city not in lean

            # 显示侧：必须出现，而且带得出数字
            full = {g["city"]: g for g in city_gaps(session, include_ok=True)}
            assert city in full
            row = full[city]
            assert row["have"] == 30
            assert row["accounts"] == 1
            assert row["short"] <= 0                    # 够用
            assert row["days_left"] and row["days_left"] > 0
        finally:
            _cleanup(session, *made)


def test_short_city_still_reported_both_ways():
    """有缺口的城市两种口径下都要在 —— 别把「显示全部」写成「只显示够用的」。"""
    create_db_and_tables()
    with Session(engine) as session:
        city = f"缺口城-{uuid.uuid4().hex[:6]}"
        made = _running_city(session, city, stock=0)
        try:
            lean = {g["city"]: g for g in city_gaps(session)}
            full = {g["city"]: g for g in city_gaps(session, include_ok=True)}
            assert city in lean and city in full
            assert lean[city]["short"] > 0
            assert lean[city]["short"] == full[city]["short"]
        finally:
            _cleanup(session, *made)
