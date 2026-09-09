"""运维告警中心 — derives active problems (offline / account health / failures)."""
import uuid
from datetime import timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.session import engine
from app.main import app
from app.core.enums import TaskStatus
from app.models.entities import (
    Device,
    DeviceAccount,
    PublishTask,
    utcnow,
)


def test_alerts_surface_offline_and_account_health():
    # Enter the app first so startup creates the tables, then seed + assert.
    with TestClient(app) as client:
        with Session(engine) as session:
            # A device whose heartbeat is long stale → should alert as offline.
            old = utcnow().replace(tzinfo=timezone.utc) - timedelta(minutes=30)
            dev = Device(
                device_code=f"alert-{uuid.uuid4().hex[:8]}",
                name="告警机",
                last_heartbeat_at=old,
                status="online",
            )
            session.add(dev)
            session.commit()
            session.refresh(dev)
            # An account flagged 限流 → should alert as account_health.
            acc = DeviceAccount(
                device_id=dev.id,
                platform="douyin",
                nickname="限流号",
                health="limited",
                health_message="疑似限流",
            )
            session.add(acc)
            session.commit()
            session.refresh(acc)
            dev_id, acc_id = dev.id, acc.id

        try:
            data = client.get("/api/v1/alerts").json()
            ids = {a["id"] for a in data["alerts"]}
            assert f"device-offline-{dev_id}" in ids
            assert f"account-health-{acc_id}" in ids
            offline = next(a for a in data["alerts"] if a["id"] == f"device-offline-{dev_id}")
            assert offline["severity"] == "critical"
            assert data["counts"]["critical"] >= 1
        finally:
            with Session(engine) as session:
                session.delete(session.get(DeviceAccount, acc_id))
                session.delete(session.get(Device, dev_id))
                session.commit()


def _seed_device(session: Session, name: str, *, heartbeat_ago_s: int) -> Device:
    dev = Device(
        device_code=f"alert-{uuid.uuid4().hex[:8]}",
        name=name,
        status="online",
        last_heartbeat_at=utcnow().replace(tzinfo=timezone.utc)
        - timedelta(seconds=heartbeat_ago_s),
    )
    session.add(dev)
    session.commit()
    session.refresh(dev)
    return dev


def test_stuck_confirmation_alerts():
    """任务停在「等人处理」超过 10 分钟 = 后台清扫没在跑。

    这是"引擎停了"最早能看见的信号，而在补这条之前控制台是**全绿**的：
    设备在线、没有失败任务，只是什么都不发。
    """
    with TestClient(app) as client:
        with Session(engine) as session:
            dev = _seed_device(session, "卡住机", heartbeat_ago_s=5)
            task = PublishTask(
                name="卡住的任务",
                platform="douyin",
                target_device_id=dev.id,
                device_id=dev.id,
                status=TaskStatus.WAITING_CONFIRMATION,
                waiting_since=utcnow().replace(tzinfo=None) - timedelta(minutes=15),
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            dev_id, task_id = dev.id, task.id

        try:
            alerts = client.get("/api/v1/alerts").json()["alerts"]
            stuck = next(a for a in alerts if a["kind"] == "task_stuck")
            assert stuck["severity"] == "critical"
            assert "卡住机" in stuck["detail"]
            # 标题要带上等了多久 —— 运营看的是这个数字决定要不要现在去重启
            assert "15 分钟" in stuck["title"]
        finally:
            with Session(engine) as session:
                session.delete(session.get(PublishTask, task_id))
                session.delete(session.get(Device, dev_id))
                session.commit()


def test_queue_backlog_alerts_only_for_online_devices():
    """到点没发 + 手机在线 = 领取链路坏了，必须报。

    手机掉线的那些**不报**：掉线本身已经报过一次，同一件事报两遍会让人
    以为是两个问题，然后两个都不查。
    """
    with TestClient(app) as client:
        with Session(engine) as session:
            live = _seed_device(session, "在线机", heartbeat_ago_s=5)
            dead = _seed_device(session, "掉线机", heartbeat_ago_s=3600)
            due = utcnow().replace(tzinfo=None) - timedelta(minutes=40)
            soon = utcnow().replace(tzinfo=None) + timedelta(hours=3)
            tasks = [
                PublishTask(name="积压1", target_device_id=live.id,
                            status=TaskStatus.QUEUED, scheduled_at=due),
                PublishTask(name="积压2", target_device_id=live.id,
                            status=TaskStatus.QUEUED, scheduled_at=due),
                # 排在将来的不算积压 —— 手动排期本来就会提前很久建好
                PublishTask(name="还没到点", target_device_id=live.id,
                            status=TaskStatus.QUEUED, scheduled_at=soon),
                PublishTask(name="掉线机的", target_device_id=dead.id,
                            status=TaskStatus.QUEUED, scheduled_at=due),
            ]
            for t in tasks:
                session.add(t)
            session.commit()
            ids = [t.id for t in tasks]
            live_id, dead_id = live.id, dead.id

        try:
            alerts = client.get("/api/v1/alerts").json()["alerts"]
            backlog = [a for a in alerts if a["kind"] == "queue_backlog"]
            assert [a["device_id"] for a in backlog] == [live_id]
            assert "2 条到点没发" in backlog[0]["title"]   # 将来那条不算
            assert "40 分钟" in backlog[0]["detail"]
            # 掉线机只报掉线，不再报一次积压
            assert f"device-offline-{dead_id}" in {a["id"] for a in alerts}
        finally:
            with Session(engine) as session:
                for tid in ids:
                    session.delete(session.get(PublishTask, tid))
                session.delete(session.get(Device, live_id))
                session.delete(session.get(Device, dead_id))
                session.commit()


def test_设备健康不能有两份真相():
    """线上撞到的：告警铃铛里挂着 7 条「设备异常：云机XXXX / 账号异常/被封禁」，
    而设备页显示这些设备全部 normal。

    根因是同一件事存了两份，而两份的写入方不对称：
    - 心跳同时写 `device.health` 和 `account.health`
    - 而运营点「标记正常」的接口**只写 account.health**

    于是 device.health 一旦被置成 abnormal 就再也回不去 —— 全站没有任何入口
    能改它。更隐蔽的是设备页看不出来：`serialize_device` 用账号的 health
    覆盖了同名字段，设备自己那份根本不出现在响应里。

    一个清不掉的红点比没有红点更糟：它把整个铃铛训练成可以无视的东西。
    """
    from app.core.config import get_settings

    settings = get_settings()
    settings.admin_token = "health-test"
    headers = {"X-Admin-Token": "health-test"}
    with TestClient(app) as client:
        with Session(engine) as session:
            dev = _seed_device(session, "两份真相机", heartbeat_ago_s=5)
            acc = DeviceAccount(
                device_id=dev.id, platform="douyin", nickname="封号候选",
                health="abnormal", health_message="账号异常/被封禁",
            )
            dev.health = "abnormal"
            dev.health_message = "账号异常/被封禁"
            session.add(dev)
            session.add(acc)
            session.commit()
            dev_id, acc_id = dev.id, acc.id

        try:
            alerts = client.get("/api/v1/alerts", headers=headers).json()["alerts"]
            mine = [a for a in alerts if a.get("device_id") == dev_id]
            # 同一件事只报一次，报的是账号那一份（「被封禁」是账号的状态）
            assert [a["kind"] for a in mine] == ["account_health"], mine

            # 运营标记正常之后，**两份都要清掉**
            r = client.patch(f"/api/v1/devices/{dev_id}/health", headers=headers,
                             json={"platform": "douyin", "health": "normal",
                                   "health_message": None})
            assert r.status_code == 200, r.text
            with Session(engine) as session:
                assert session.get(Device, dev_id).health == "normal", \
                    "设备上那份影子副本没被清掉 —— 告警会永远挂着"
                assert session.get(DeviceAccount, acc_id).health == "normal"
            assert not [a for a in client.get("/api/v1/alerts", headers=headers).json()["alerts"]
                        if a.get("device_id") == dev_id]

            # 没有账号的设备（还没登号的空机）仍然要报设备级异常，别把这条也丢了
            with Session(engine) as session:
                bare = _seed_device(session, "空机", heartbeat_ago_s=5)
                bare.health = "abnormal"
                bare.health_message = "存储空间不足"
                session.add(bare)
                session.commit()
                bare_id = bare.id
            kinds = [a["kind"] for a in client.get("/api/v1/alerts", headers=headers).json()["alerts"]
                     if a.get("device_id") == bare_id]
            assert kinds == ["device_health"], kinds
            with Session(engine) as session:
                session.delete(session.get(Device, bare_id))
                session.commit()
        finally:
            settings.admin_token = None
            with Session(engine) as session:
                session.delete(session.get(DeviceAccount, acc_id))
                session.delete(session.get(Device, dev_id))
                session.commit()


def test_无障碍要分清抖动和卡死():
    """用户定的：「除非手机卡死，其他情况不需要重启手机，因为我们做了发布失败
    会自动排期到今日的后续的一个时间来重新发布」。

    数据支持这个判断 —— 2026-09-08 实测当天 7 台掉过无障碍，**6 台几分钟内
    自己回来了**，而那天它们一篇都没耽误。所以短暂掉线不该报：
    每天几十条「抖了一下」会把铃铛训练成可以无视的东西。

    只有两种情况值得占运营一次注意力：
    ① 掉超过 15 分钟（编排器已经自愈三轮没成）；
    ② 而且要分「这个号正在少发」和「这个号本来就没在跑」。
    """
    with TestClient(app) as client:
        with Session(engine) as session:
            flap = _seed_device(session, "抖一下机", heartbeat_ago_s=5)
            stuck_idle = _seed_device(session, "卡住闲置机", heartbeat_ago_s=5)
            stuck_busy = _seed_device(session, "卡住在跑机", heartbeat_ago_s=5)
            now = utcnow().replace(tzinfo=None)
            for dev, ago in ((flap, 4), (stuck_idle, 47), (stuck_busy, 47)):
                dev.accessibility_ok = False
                dev.accessibility_since = now - timedelta(minutes=ago)
                session.add(dev)
            session.add(DeviceAccount(
                device_id=stuck_idle.id, platform="douyin", nickname="没开自动化的号",
            ))
            session.add(DeviceAccount(
                device_id=stuck_busy.id, platform="douyin", nickname="小满每日",
                auto_publish=True,
            ))
            session.commit()
            ids = [flap.id, stuck_idle.id, stuck_busy.id]

        try:
            alerts = client.get("/api/v1/alerts").json()["alerts"]
            by_dev = {a["device_id"]: a for a in alerts if a["kind"] == "a11y_unbound"}

            # ① 刚掉 4 分钟的不报 —— 它大概率自己就回来了
            assert ids[0] not in by_dev, "抖一下不该占运营一次注意力"

            # ② 卡住但没开自动化：报，但不是 critical，而且要说清「不影响出稿」
            idle = by_dev[ids[1]]
            assert idle["severity"] == "warning"
            assert "47 分钟" in idle["detail"]
            assert "不影响出稿" in idle["detail"]

            # ③ 卡住且正在少发：critical，并且要点名是哪个号
            busy = by_dev[ids[2]]
            assert busy["severity"] == "critical"
            assert "小满每日" in busy["detail"]
            assert "今天的篇数会少" in busy["detail"]
        finally:
            with Session(engine) as session:
                for did in ids:
                    for a in session.exec(
                        select(DeviceAccount).where(DeviceAccount.device_id == did)
                    ).all():
                        session.delete(a)
                    session.delete(session.get(Device, did))
                session.commit()


def test_掉线时刻只在翻转时打():
    """`accessibility_since` 每次心跳都刷新的话，「掉了多久」永远是 0 ——
    那个数就白记了，上面那条分档规则也就永远不会触发。"""
    from app.services.device_auth import hash_device_token

    with TestClient(app) as client:
        with Session(engine) as session:
            dev = _seed_device(session, "翻转机", heartbeat_ago_s=5)
            # 心跳要设备令牌，给它发一个
            dev.token_hash = hash_device_token("beat-token")
            session.add(dev)
            session.commit()
            dev_id = dev.id
        try:
            def beat(ok):
                r = client.post(
                    f"/api/v1/devices/{dev_id}/heartbeat",
                    headers={"X-Agent-Token": "beat-token"},
                    json={"status": "online", "accessibility_ok": ok},
                )
                assert r.status_code == 200, r.text
                return r

            beat(False)
            with Session(engine) as session:
                first = session.get(Device, dev_id).accessibility_since
            assert first is not None, "掉的时候要记下起点"

            beat(False)          # 还在掉着 —— 起点不能被刷新
            with Session(engine) as session:
                assert session.get(Device, dev_id).accessibility_since == first

            beat(True)           # 恢复 —— 清掉
            with Session(engine) as session:
                d = session.get(Device, dev_id)
                assert d.accessibility_since is None and d.accessibility_ok is True
        finally:
            with Session(engine) as session:
                session.delete(session.get(Device, dev_id))
                session.commit()
