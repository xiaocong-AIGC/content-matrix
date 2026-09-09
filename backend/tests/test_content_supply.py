"""内容自动补货。

两条是这套东西的命门，都在 docs/自动发布方案.md 第 4 节用真实数据论证过：

**① 城市决定"写哪里"，不决定"学谁"。** 北京自己的 TOP10 互动只有上海/深圳的五分之一，
按"本城优先"它会永远凑得满、永远不降级，于是每天学自己一个月前最差的那批、产物再
回流样本池 —— 自我强化的低水平循环。

**② 地名泄漏必须确定性拦截。** 生成温度 1.0、样本全是外地爆款、自动过审时无人过目，
LLM 对负向指令的遵守率一定不是 100%。单次污染的后果是一个北京号发了篇写上海商圈的
探店文 —— 对外可见的运营事故。
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.enums import ContentStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import ContentItem, Device, DeviceAccount, PublishTask, utcnow
from app.services import content_supply, settings_store


def _account(session, code, city, nickname):
    device = session.exec(select(Device).where(Device.device_code == code)).first()
    if device is None:
        device = Device(device_code=code, name=f"云机-{code}")
        session.add(device)
        session.commit()
        session.refresh(device)
    session.add(DeviceAccount(device_id=device.id, platform="douyin",
                              nickname=nickname, city=city))
    # 只有最近 48 小时发过的号才算在册（和通知同一个口径）
    session.add(PublishTask(
        name=f"seed-{code}", platform="douyin", publish_type="text",
        target_device_id=device.id, device_id=device.id,
        status=TaskStatus.SUCCEEDED, finished_at=utcnow() - timedelta(hours=10),
    ))
    session.commit()
    return device


@pytest.fixture(autouse=True)
def _cleanup_after():
    """跑完把本文件造的账号和内容清掉。

    ⚠ 这几个用例会往内容库里塞「上海」「杭州」的稿子，而 `_match_content` 取的是
    **最旧的一条** —— 留在库里会把后面 test_flow 里新建的同城内容顶掉，
    表现成一个跟本文件毫无关系的用例莫名其妙地红。整个测试套共用一个库，
    往共享表里写东西的用例必须自己收尾。
    """
    yield
    create_db_and_tables()
    with Session(engine) as session:
        _reset(session)


def _reset(session):
    for row in session.exec(select(DeviceAccount)).all():
        session.delete(row)
    for row in session.exec(select(ContentItem)).all():
        session.delete(row)
    session.commit()


def test_按城市算缺口_只给在册的号算():
    """分母只算最近 48 小时发过的号 —— 否则会为走别的方式发布的号凭空生成内容，
    白花钱还把库存撑大。"""
    create_db_and_tables()
    with Session(engine) as session:
        _reset(session)
        for i in range(3):
            _account(session, f"sup-sh{i}", "上海", f"沪{i}")
        settings_store.put(session, content_supply.DAYS_KEY, "3")
        # 上海 3 个号 × 每天 1 篇 × 3 天 = 9 篇；现在只有 2 篇
        for i in range(2):
            session.add(ContentItem(cover_title=f"沪稿{i}", body="x" * 60, city="上海"))
        session.commit()
        gaps = {g["city"]: g for g in content_supply.city_gaps(session)}

    assert "上海" in gaps
    assert gaps["上海"]["need"] == 9 and gaps["上海"]["have"] == 2
    assert gaps["上海"]["short"] == 7
    assert "北京" not in gaps, "北京一个在册的号都没有，不该给它生成内容"


def test_库存够了就不生成():
    """水位驱动，不是每天无脑生成 —— 那样内容会堆到发不完还白花钱。"""
    create_db_and_tables()
    with Session(engine) as session:
        _reset(session)
        _account(session, "sup-full", "杭州", "杭州号")
        settings_store.put(session, content_supply.DAYS_KEY, "3")
        for i in range(20):
            session.add(ContentItem(cover_title=f"杭稿{i}", body="x" * 60, city="杭州"))
        session.commit()
        gaps = {g["city"] for g in content_supply.city_gaps(session)}
    assert "杭州" not in gaps, "库存够 3 天了还在报缺口"


def test_地名泄漏必须被确定性拦下():
    """这是硬闸，不能靠 prompt。一个北京号发出一篇写上海商圈的探店文
    是对外可见的运营事故，而生成温度 1.0 + 样本全是外地爆款，
    LLM 的遵守率在这个量级下一定不是 100%。"""
    create_db_and_tables()
    with Session(engine) as session:
        _reset(session)
        settings_store.put(
            session, content_supply.CITY_TOKENS_KEY,
            "上海:外滩|陆家嘴|静安寺;北京:三里屯|国贸",
        )
        ok, why = content_supply.check_draft(
            session, city="北京",
            cover="北京周末去哪玩",
            body="周末可以去陆家嘴那边走走，江景很好，人也不算太多。" + "补" * 40,
        )
        assert ok is False, "写着上海地名的稿子被放进了北京"
        assert "陆家嘴" in why and "上海" in why, why

        good, _ = content_supply.check_draft(
            session, city="北京",
            cover="北京周末去哪玩",
            body="周末可以去三里屯那边走走，人不算太多，逛完还能顺路吃个饭。" + "补" * 40,
        )
        assert good is True


def test_机器闸的其它几条():
    create_db_and_tables()
    with Session(engine) as session:
        _reset(session)
        settings_store.put(session, content_supply.CITY_TOKENS_KEY, "")
        short_ok, why = content_supply.check_draft(
            session, city="上海", cover="标题", body="太短了")
        assert short_ok is False and "短" in why

        # 写死日期的内容过几天就废了
        dated_ok, why = content_supply.check_draft(
            session, city="上海", cover="标题",
            body="这个活动本周六开始，别错过了。" + "补" * 40)
        assert dated_ok is False and ("日期" in why or "星期" in why), why

        # 封面撞库
        session.add(ContentItem(cover_title="一模一样的封面", body="x" * 60, city="上海"))
        session.commit()
        dup_ok, why = content_supply.check_draft(
            session, city="上海", cover="一模一样的封面", body="不一样的正文" + "补" * 40)
        assert dup_ok is False and "重" in why


def test_没开启时什么都不做():
    """默认关着 —— 这会真的花钱、真的往内容库里写东西。"""
    create_db_and_tables()
    with Session(engine) as session:
        settings_store.put(session, content_supply.ENABLED_KEY, "")
        assert content_supply.run_once(session).get("skipped")


def test_地名词表可以单独保存_不碰生成参数():
    """`PUT /supply` 收的是整个设置对象：只想给新城市补一行地名，也会把四个生成
    参数一并写回。而这张词表是「正文里出现别的城市地名就不入库」这道硬闸的全部依据，
    被另一次保存覆盖掉，闸就静默失效 —— 失效的表现是一篇写着上海商圈的文案挂在
    北京号上，读者看得到。所以它要有自己的接口。"""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    settings = get_settings()
    settings.admin_token = "supply-test"
    headers = {"X-Admin-Token": "supply-test"}
    try:
        with TestClient(app) as client:
            client.put("/api/v1/supply", headers=headers, json={
                "enabled": False, "min_days": 5, "batch_cap": 20,
                "quality_floor": 200, "city_tokens_raw": "上海:外滩",
            })
            r = client.put("/api/v1/supply/city-tokens", headers=headers,
                           json={"city_tokens_raw": "上海:外滩|静安;北京:国贸"})
            assert r.status_code == 200, r.text
            body = r.json()
            # 词表改了
            assert body["city_tokens"] == {"上海": ["外滩", "静安"], "北京": ["国贸"]}
            # 四个生成参数一个都没被动
            assert body["min_days"] == 5 and body["batch_cap"] == 20
            assert body["quality_floor"] == 200 and body["enabled"] is False
    finally:
        settings.admin_token = None


def test_自动生成按城市开_不是一个总开关():
    """用户原话：「自动生成应该按城市来开启，而不是一起生成」。

    各城市的库存、内容质量、投放节奏都不一样 —— 上海缺 14 篇，
    不代表北京也该开着写。
    """
    from sqlmodel import Session

    from app.db.session import create_db_and_tables, engine
    from app.services import content_supply, settings_store

    create_db_and_tables()
    with Session(engine) as session:
        settings_store.put(session, content_supply.CITIES_KEY, "")
        settings_store.put(session, content_supply.ENABLED_KEY, "0")
        assert content_supply.enabled_cities(session) == set()
        assert content_supply.enabled(session) is False
        assert content_supply.run_once(session).get("skipped") == "没有开启任何城市"

        settings_store.put(session, content_supply.CITIES_KEY, "上海, 深圳")
        assert content_supply.enabled_cities(session) == {"上海", "深圳"}
        assert content_supply.enabled(session) is True

        # 老的总开关还认：没配 supply:cities 时视作全开（兼容旧数据）
        settings_store.put(session, content_supply.CITIES_KEY, "")
        settings_store.put(session, content_supply.ENABLED_KEY, "1")
        assert content_supply.enabled(session) is True
        settings_store.put(session, content_supply.ENABLED_KEY, "0")


def test_打开城市就立刻开始生成_不等轮询():
    """用户原话：「打开自动生成后应该是立即开始生成缺少的笔记，
    补货循环应该是检测到数量低了后自动生成，为什么要做成定时循环的呢」。

    这条是对的：运营是**盯着屏幕**点的那一下。「打开开关」和「开始生成」之间
    只要有一段什么都不发生的空档，在他看来就是坏了 —— 然后他会去点
    「现在生成一批」，反而多花一次钱。

    生成本身不能挂在这个请求上（调模型要几十秒，按钮会像卡死），
    所以这里断言的是：保存之后**后台那一轮被立刻叫醒了**，而且是 force
    （不受冷却约束）。
    """
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app
    from app.services import supply_trigger

    settings = get_settings()
    settings.admin_token = "supply-poke"
    headers = {"X-Admin-Token": "supply-poke"}
    try:
        with TestClient(app) as client:
            supply_trigger.take()  # 清掉之前的测试攒下的
            r = client.put("/api/v1/supply", headers=headers, json={
                "enabled": True, "enabled_cities": ["深圳"], "min_days": 3,
                "batch_cap": 10, "quality_floor": 120, "city_tokens_raw": "",
            })
            assert r.status_code == 200, r.text
            reasons, forced = supply_trigger.take()
            assert forced is True, "开城市必须跳过冷却，立刻跑"
            assert any("深圳" in x for x in reasons)

            # 一个城市都不开时不该叫醒（那一轮跑了也是直接跳过，白占一次）
            client.put("/api/v1/supply", headers=headers, json={
                "enabled": False, "enabled_cities": [], "min_days": 3,
                "batch_cap": 10, "quality_floor": 120, "city_tokens_raw": "",
            })
            assert supply_trigger.take() == ([], False)
    finally:
        settings.admin_token = None
        supply_trigger.take()


def test_发出去一篇就叫醒补货():
    """「检测到数量低了后自动生成」—— 水位下降这件事本身就是触发点。

    内容从池里出去（PENDING → PUBLISHED）是唯一一个"库存真的少了一篇"的时刻，
    补货挂在这里，而不是等定时器隔半小时想起来看一眼。
    """
    from app.services import supply_trigger
    from app.services.tasks import _finalize_content

    create_db_and_tables()
    with Session(engine) as session:
        device = _account(session, "poke-dev", "深圳", "水位号").id
        item = ContentItem(
            title="t", cover_title="c", body="正文" * 30, city="深圳",
            platform="douyin", status=ContentStatus.PUBLISHING,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        task = PublishTask(
            name="发一篇", platform="douyin", target_device_id=device,
            device_id=device, content_id=item.id, status=TaskStatus.SUCCEEDED,
            finished_at=utcnow().replace(tzinfo=None),
        )
        session.add(task)
        session.commit()

        supply_trigger.take()
        _finalize_content(session, task, succeeded=True)
        session.commit()
        reasons, forced = supply_trigger.take()
        assert any("深圳" in x for x in reasons), reasons
        # 发布触发的这一次要受冷却约束：一波任务同时结束会连打好几次
        assert forced is False

        # 已经是 PUBLISHED 了再收一次（另一个平台的兄弟任务收尾）不该重复叫醒
        _finalize_content(session, task, succeeded=True)
        assert supply_trigger.take() == ([], False)

        session.delete(session.get(PublishTask, task.id))
        session.delete(session.get(ContentItem, item.id))
        session.commit()
