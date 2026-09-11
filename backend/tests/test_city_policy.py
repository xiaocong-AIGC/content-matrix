"""城市策略的解析顺序：账号例外 → 城市策略 → 兜底。

**最要紧的是第一个用例：上线当天行为零变化。** 城市策略改的是每一条发布
怎么被调度，解析错一处，表现就是「深圳突然不发了」或者「某个城市自己
开始发了」，而且不会有任何报错。
"""

import uuid

from sqlmodel import Session, select

from app.db.session import create_db_and_tables, engine
from app.models.entities import AppSetting, CityPolicy, Device, DeviceAccount
from app.services import city_policy as cp
from app.services.tasks import PUBLISH_WINDOWS_KEY


def _city() -> str:
    # 全套测试共用一个库，城市名必须唯一
    return f"策略城-{uuid.uuid4().hex[:6]}"


def _account(session, city: str, *, on: bool, quota: int = 3, override=None):
    dev = Device(device_code=f"cp-{uuid.uuid4().hex[:10]}", name="云机")
    session.add(dev)
    session.commit()
    session.refresh(dev)
    acc = DeviceAccount(
        device_id=dev.id, platform="douyin", nickname=f"号{uuid.uuid4().hex[:4]}",
        city=city, auto_publish=on, daily_quota=quota, health="normal",
        publish_override=override,
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return dev, acc


def _cleanup(session, *rows):
    for row in rows:
        obj = session.get(type(row), row.id)
        if obj is not None:
            session.delete(obj)
    session.commit()


def _drop_policy(session, city):
    for p in session.exec(select(CityPolicy).where(CityPolicy.city == city)).all():
        session.delete(p)
    session.commit()


def test_no_policy_means_exactly_the_old_behaviour():
    """城市没设策略 → 每个号都按自己的老开关。这就是上线当天的样子。"""
    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        d1, on = _account(s, city, on=True)
        d2, off = _account(s, city, on=False)
        try:
            assert cp.publishes(s, on) is True
            assert cp.publishes(s, off) is False
        finally:
            _cleanup(s, on, off, d1, d2)


def test_city_switch_beats_the_old_account_switch():
    """城市开着，老开关是关的号也要发 —— 这正是「加一个深圳号不用再去勾」。"""
    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        d, acc = _account(s, city, on=False)
        try:
            cp.set_policy(s, city, auto_publish=True)
            assert cp.publishes(s, acc) is True
            cp.set_policy(s, city, auto_publish=False)
            assert cp.publishes(s, acc) is False
            # 城市改回「不管开关」→ 回到老开关
            cp.set_policy(s, city, auto_publish=None)
            assert cp.publishes(s, acc) is False
        finally:
            _drop_policy(s, city)
            _cleanup(s, acc, d)


def test_account_exception_beats_the_city():
    """全城开着，这个号强制关；全城关着，这个号强制开。"""
    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        d1, stop = _account(s, city, on=True, override=cp.OVERRIDE_OFF)
        d2, go = _account(s, city, on=False, override=cp.OVERRIDE_ON)
        try:
            cp.set_policy(s, city, auto_publish=True)
            assert cp.publishes(s, stop) is False
            cp.set_policy(s, city, auto_publish=False)
            assert cp.publishes(s, go) is True
        finally:
            _drop_policy(s, city)
            _cleanup(s, stop, go, d1, d2)


def test_windows_fall_back_to_global_and_empty_means_unrestricted():
    """城市没设时段 → 全局；设成空串 → 这个城市明确不限时段（和没设不是一回事）。"""
    from app.services import settings_store

    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        d, acc = _account(s, city, on=True)
        old = settings_store.get(s, PUBLISH_WINDOWS_KEY, "")
        try:
            settings_store.put(s, PUBLISH_WINDOWS_KEY, "10-11,14-15")
            assert len(cp.windows_for(s, acc)) == 2          # 跟全局

            cp.set_policy(s, city, windows="17-18")
            assert cp.windows_for(s, acc) == [(17 * 60, 18 * 60)]  # 城市自己的

            cp.set_policy(s, city, windows="")
            assert cp.windows_for(s, acc) == []              # 明确不限

            cp.set_policy(s, city, windows=None)
            assert len(cp.windows_for(s, acc)) == 2          # 改回跟全局
        finally:
            settings_store.put(s, PUBLISH_WINDOWS_KEY, old)
            _drop_policy(s, city)
            _cleanup(s, acc, d)


def test_city_target_wins_but_global_still_respects_the_cap():
    """城市跟全局 → min(全局, 单号上限)，和改之前一样；城市明确设了篇数 → 就是它。

    ⚠ 这条用例原先断言「城市设 5、上限 1 → 1」，而且只因为 roomy 用了
    quota=10 才通过。生产上所有号的上限都被保存默认设置覆盖成全局值（3），
    照原规则城市设 5 永远只发 3 —— 设置不生效、界面照写 5 篇。
    """
    from app.services.notify_sweep import DAILY_TARGET_KEY
    from app.services import settings_store

    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        d1, roomy = _account(s, city, on=True, quota=10)
        d2, capped = _account(s, city, on=True, quota=1)
        old = settings_store.get(s, DAILY_TARGET_KEY, "")
        try:
            settings_store.put(s, DAILY_TARGET_KEY, "3")
            assert cp.target_for(s, roomy) == 3              # 全局 3
            assert cp.target_for(s, capped) == 1             # 被上限压到 1

            cp.set_policy(s, city, daily_target=5)
            assert cp.target_for(s, roomy) == 5              # 城市明确设了 5
            assert cp.target_for(s, capped) == 5             # 就是 5，不再被上限压
        finally:
            settings_store.put(s, DAILY_TARGET_KEY, old)
            _drop_policy(s, city)
            _cleanup(s, roomy, capped, d1, d2)


def test_seed_only_for_cities_that_agree():
    """折算只给全城一致的城市建策略；开关不一致的城市不建，各号照旧。"""
    create_db_and_tables()
    with Session(engine) as s:
        all_on, mixed = _city(), _city()
        d1, a1 = _account(s, all_on, on=True)
        d2, a2 = _account(s, all_on, on=True)
        d3, b1 = _account(s, mixed, on=True)
        d4, b2 = _account(s, mixed, on=False)
        try:
            before = {a.id: cp.publishes(s, a) for a in (a1, a2, b1, b2)}
            made = cp.seed_from_accounts(s)
            assert all_on in made
            assert mixed not in made
            after = {a.id: cp.publishes(s, a) for a in (a1, a2, b1, b2)}
            assert before == after, "折算前后必须没有任何一个号的发不发变了"
        finally:
            _drop_policy(s, all_on)
            _drop_policy(s, mixed)
            _cleanup(s, a1, a2, b1, b2, d1, d2, d3, d4)


def test_not_a_city_cannot_have_a_policy():
    """「未分组」不是城市 —— 号没设城市是漏配了，不是一个分组。"""
    create_db_and_tables()
    with Session(engine) as s:
        for name in ("未分组", "通用", ""):
            try:
                cp.set_policy(s, name, auto_publish=True)
            except ValueError:
                continue
            raise AssertionError(f"「{name}」不该能挂策略")
        assert cp.policy_for(s, "未分组") is None


def test_old_toggle_still_works_when_the_city_has_a_switch():
    """城市设了开关之后，老的单号开关不能变成静默失败。

    桌面版 app.exe 里编译的还是老前端，运营点老按钮 —— 如果只写老开关，
    接口返回成功、号却一动不动（城市优先）。所以老开关要被翻译成账号例外。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        dev, acc = _account(s, city, on=False)
        cp.set_policy(s, city, auto_publish=False)
        # session 关掉之后对象就脱管了，再读 .id 会抛 DetachedInstanceError —— 先取出来
        dev_id, acc_id = dev.id, acc.id
    try:
        with TestClient(app) as client:
            # 全城关着，单独点开这个号 → 必须真的开
            client.patch(f"/api/v1/devices/{dev_id}/auto-publish",
                         json={"auto_publish": True})
            with Session(engine) as s:
                a = s.get(DeviceAccount, acc_id)
                assert a.publish_override == cp.OVERRIDE_ON
                assert cp.publishes(s, a) is True

            # 再点关 → 和城市一致，例外应被清掉，而不是留一条冗余的「强制关」
            client.patch(f"/api/v1/devices/{dev_id}/auto-publish",
                         json={"auto_publish": False})
            with Session(engine) as s:
                a = s.get(DeviceAccount, acc_id)
                assert a.publish_override is None
                assert cp.publishes(s, a) is False
    finally:
        with Session(engine) as s:
            _drop_policy(s, city)
            _cleanup(s, s.get(DeviceAccount, acc_id), s.get(Device, dev_id))


def test_moving_city_clears_the_exception():
    """换城市要清掉账号例外 —— 搬进未分组后新页面看不到它，号会被一条看不见的例外顶着一直发。"""
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as s:
        dev, acc = _account(s, _city(), on=False, override=cp.OVERRIDE_ON)
        dev_id, acc_id = dev.id, acc.id
    try:
        with TestClient(app) as client:
            client.patch(f"/api/v1/devices/{dev_id}/profile", json={"city": "未分组"})
        with Session(engine) as s:
            a = s.get(DeviceAccount, acc_id)
            assert a.publish_override is None
            assert cp.publishes(s, a) is False
    finally:
        with Session(engine) as s:
            _cleanup(s, s.get(DeviceAccount, acc_id), s.get(Device, dev_id))


def test_old_toggle_clears_exception_when_city_has_no_switch():
    """城市没设开关（未分组 / 折算后才出现的城市）时，老开关点关必须真的关。

    以前那段翻译只在城市设了开关时才跑：点关只写老开关，例外还在，
    publishes() 先看例外 —— 返回 200、号照发。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as s:
        dev, acc = _account(s, "未分组", on=True, override=cp.OVERRIDE_ON)
        dev_id, acc_id = dev.id, acc.id
    try:
        with TestClient(app) as client:
            client.patch(f"/api/v1/devices/{dev_id}/auto-publish", json={"auto_publish": False})
        with Session(engine) as s:
            a = s.get(DeviceAccount, acc_id)
            assert a.publish_override is None
            assert cp.publishes(s, a) is False
    finally:
        with Session(engine) as s:
            _cleanup(s, s.get(DeviceAccount, acc_id), s.get(Device, dev_id))


def test_device_payload_carries_the_effective_switch():
    """下发给客户端的单号 auto_publish 必须是实际值。

    桌面版 app.exe 编译的是老前端，列表、计数、「全部关闭」全靠这个字段 ——
    下发原值的话，城市开着的号在老界面上显示「不参与」，「全部关闭」也关不到它。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as s:
        city = _city()
        dev, acc = _account(s, city, on=False)     # 老开关是关的
        cp.set_policy(s, city, auto_publish=True)  # 城市开着
        dev_id, acc_id = dev.id, acc.id            # session 关了之后对象脱管，先取出来
    try:
        with TestClient(app) as client:
            # 没有单台设备的 GET 接口，客户端（包括老版 app.exe）拿的都是这个列表
            listing = client.get("/api/v1/devices").json()
        payload = next(d for d in listing if d["id"] == dev_id)
        a = next(x for x in payload["accounts"] if x["platform"] == "douyin")
        assert a["auto_publish"] is True           # 实际在发
        assert a["auto_publish_raw"] is False      # 原值另起字段
        assert a["target"] >= 1
    finally:
        with Session(engine) as s:
            _drop_policy(s, city)
            _cleanup(s, s.get(DeviceAccount, acc_id), s.get(Device, dev_id))
