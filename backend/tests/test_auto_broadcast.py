"""群推送自动化。

最要紧的一条是**幂等**：排程器跑在后台 loop 里，反复跑、进程重启都不能重复排 ——
一条群消息被重复推到真实的群里，是运营要去道歉的事故，不是一个数字错了。
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.enums import TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import (
    BroadcastMessage,
    ChatGroup,
    Device,
    DeviceAccount,
    PublishTask,
)
from app.services import auto_broadcast, settings_store
from app.services.schedule_windows import CN_TZ


def _setup(session, *, count=2, windows="10-11,14-15", mode="same", accounts=1):
    for row in session.exec(select(PublishTask)).all():
        if row.publish_type == "group_message":
            session.delete(row)
    for row in session.exec(select(BroadcastMessage)).all():
        session.delete(row)
    for row in session.exec(select(DeviceAccount)).all():
        session.delete(row)
    for row in session.exec(select(ChatGroup)).all():
        session.delete(row)
    session.commit()
    settings_store.put(session, auto_broadcast.COUNT_KEY, str(count))
    settings_store.put(session, auto_broadcast.WINDOWS_KEY, windows)
    settings_store.put(session, auto_broadcast.MODE_KEY, mode)
    session.add(BroadcastMessage(text="今天新房源已更新，需要的滴滴", city="通用"))
    session.add(BroadcastMessage(text="周末看房班车照常发车", city="通用"))
    session.commit()
    made = []
    for i in range(accounts):
        device = session.exec(
            select(Device).where(Device.device_code == f"bc-{i}")
        ).first()
        if device is None:
            device = Device(device_code=f"bc-{i}", name=f"云机70{i}")
            session.add(device)
            session.commit()
            session.refresh(device)
        session.add(ChatGroup(device_id=device.id, group_name=f"看房群{i}"))
        account = DeviceAccount(
            device_id=device.id, platform="douyin", nickname=f"推送号{i}",
            city="上海", auto_broadcast=True,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        made.append(account)
    return made


def _evening():
    """中国时间 18:00 —— 两个时间段都过去超过 2 小时（补发窗口）了。"""
    return datetime(2026, 9, 4, 18, 0, tzinfo=CN_TZ).astimezone(timezone.utc)


def test_按时间段把当天该推的排出来():
    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        made = auto_broadcast.plan_for_account(session, account, _evening())
        assert made == 0, "两个时段都过去超过 2 小时了，不该补"

        # 14:30：第一段(10-11)已经过去太久跳过，第二段(14-15)刚过、在补发窗口内
        early = datetime(2026, 9, 4, 14, 50, tzinfo=CN_TZ).astimezone(timezone.utc)
        made = auto_broadcast.plan_for_account(session, account, early)
        tasks = session.exec(
            select(PublishTask).where(PublishTask.publish_type == "group_message")
        ).all()
    assert made == 1, f"该补 14-15 那一次，实际 {made}"
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == TaskStatus.QUEUED
    assert task.scheduled_at is not None
    assert "看房群0" in task.target_groups_json
    assert task.body


def test_反复排不会重复推():
    """后台 loop 每 2 分钟跑一次，重复推到真实群里是要道歉的事故。"""
    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)
        first = auto_broadcast.plan_for_account(session, account, moment)
        again = sum(
            auto_broadcast.plan_for_account(session, account, moment) for _ in range(5)
        )
        tasks = session.exec(
            select(PublishTask).where(PublishTask.publish_type == "group_message")
        ).all()
    assert first >= 1
    assert again == 0, f"重复排了 {again} 条"
    assert len(tasks) == first


def test_同一天所有号发同一条_或者各发各的():
    create_db_and_tables()
    with Session(engine) as session:
        accounts = _setup(session, count=1, windows="10-11", mode="same", accounts=4)
        moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)
        for a in accounts:
            auto_broadcast.plan_for_account(session, a, moment)
        same_texts = {
            t.body for t in session.exec(
                select(PublishTask).where(PublishTask.publish_type == "group_message")
            ).all()
        }
    assert len(same_texts) == 1, f"same 模式下所有号该发同一条：{same_texts}"

    with Session(engine) as session:
        accounts = _setup(session, count=1, windows="10-11", mode="rotate", accounts=6)
        moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)
        for a in accounts:
            auto_broadcast.plan_for_account(session, a, moment)
        rotate_texts = {
            t.body for t in session.exec(
                select(PublishTask).where(PublishTask.publish_type == "group_message")
            ).all()
        }
    assert len(rotate_texts) > 1, "rotate 模式下不同号该挑到不同的消息"


def test_关掉_没群_没消息_都不排():
    create_db_and_tables()
    moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)

    with Session(engine) as session:          # 次数为 0
        account = _setup(session, count=0)[0]
        assert auto_broadcast.plan_for_account(session, account, moment) == 0

    with Session(engine) as session:          # 账号没开开关
        account = _setup(session)[0]
        account.auto_broadcast = False
        session.add(account)
        session.commit()
        assert auto_broadcast.plan_for_account(session, account, moment) == 0

    with Session(engine) as session:          # 这台机还没扫到群
        account = _setup(session)[0]
        for g in session.exec(select(ChatGroup)).all():
            session.delete(g)
        session.commit()
        assert auto_broadcast.plan_for_account(session, account, moment) == 0

    with Session(engine) as session:          # 消息库空的
        account = _setup(session)[0]
        for m in session.exec(select(BroadcastMessage)).all():
            session.delete(m)
        session.commit()
        assert auto_broadcast.plan_for_account(session, account, moment) == 0


def test_只用得到本城和通用的消息():
    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session, count=1, windows="10-11")[0]
        for m in session.exec(select(BroadcastMessage)).all():
            session.delete(m)
        session.add(BroadcastMessage(text="北京专用，不该发到上海", city="北京"))
        session.commit()
        moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)
        made = auto_broadcast.plan_for_account(session, account, moment)
    assert made == 0, "上海的号不该用北京的消息"


# ---------------------------------------------------------------------------
# 自动发布也走同一套时间段
# ---------------------------------------------------------------------------
def test_没配时间段时自动发布行为不变():
    """默认没配 —— 这时候必须完全是老行为（手机空了就发），不能因为加了这道闸
    把生产上正在跑的自动发布拦住。"""
    from app.services.tasks import PUBLISH_WINDOWS_KEY, _due_by_plan

    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        account.auto_publish = True
        session.add(account)
        settings_store.put(session, PUBLISH_WINDOWS_KEY, "")
        session.commit()
        assert _due_by_plan(session, account, datetime.now(timezone.utc)) is True


def test_配了时间段就按计划放行():
    """每天 N 条、每条落在段内一个随机时刻：到点之前不放行，到点之后放行一条。"""
    from app.services.notify_sweep import DAILY_TARGET_KEY
    from app.services.tasks import PUBLISH_WINDOWS_KEY, _due_by_plan

    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        account.auto_publish = True
        session.add(account)
        settings_store.put(session, PUBLISH_WINDOWS_KEY, "10-11,14-15")
        settings_store.put(session, DAILY_TARGET_KEY, "2")
        session.commit()

        dawn = datetime(2026, 9, 4, 8, 0, tzinfo=CN_TZ).astimezone(timezone.utc)
        assert _due_by_plan(session, account, dawn) is False, "还没到第一个点就放行了"

        noon = datetime(2026, 9, 4, 12, 0, tzinfo=CN_TZ).astimezone(timezone.utc)
        assert _due_by_plan(session, account, noon) is True, "第一个点过了该放行"

        # 已经发了一条 → 第二个点之前不再放行
        session.add(PublishTask(
            name="今天第一条", platform="douyin", publish_type="text",
            target_device_id=account.device_id, device_id=account.device_id,
            status=TaskStatus.SUCCEEDED,
        ))
        session.commit()
        assert _due_by_plan(session, account, noon) is False, "一天两条却在中午放行了第二条"

        night = datetime(2026, 9, 4, 20, 0, tzinfo=CN_TZ).astimezone(timezone.utc)
        assert _due_by_plan(session, account, night) is True, "第二个点过了该放行"
        settings_store.put(session, DAILY_TARGET_KEY, "")
        settings_store.put(session, PUBLISH_WINDOWS_KEY, "")


def test_没扫到群不能静默跳过():
    """开着自动推送、却一个群都没有 —— 表现是这个号每天安安静静推 0 条，
    界面上和"推成功了"长得一模一样。群列表是 Agent 启动时扫一次的（还是先删后插），
    所以"群没了"是真实且常见的状态，必须说出来。"""
    from app.models.entities import AlertDispatch

    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        for g in session.exec(select(ChatGroup)).all():
            session.delete(g)
        for row in session.exec(select(AlertDispatch)).all():
            session.delete(row)
        session.commit()
        moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)
        made = auto_broadcast.plan_for_account(session, account, moment)

    with Session(engine) as session:
        alerts = session.exec(select(AlertDispatch)).all()
    assert made == 0
    assert any(a.event == "broadcast_no_groups" for a in alerts), (
        "一个群都没有却什么都没说 —— 这个号会每天静默推 0 条"
    )


def test_过点太久的群推送作废而不是半夜发出去():
    """领任务只看 scheduled_at <= now，没有过期概念：设备忙或离线时，早上 10 点该推的
    可能半夜才被领走。作品发晚了还能接受，**群消息发晚了是打扰**。"""
    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        stale = PublishTask(
            name=f"{auto_broadcast.AUTO_NAME_PREFIX}早上该推的",
            publish_type="group_message", body="早上该推的",
            target_device_id=account.device_id, status=TaskStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=9),
        )
        manual = PublishTask(
            name="群发：运营自己定时的", publish_type="group_message", body="手动的",
            target_device_id=account.device_id, status=TaskStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=9),
        )
        fresh = PublishTask(
            name=f"{auto_broadcast.AUTO_NAME_PREFIX}刚才该推的",
            publish_type="group_message", body="刚才的",
            target_device_id=account.device_id, status=TaskStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=1),
        )
        session.add_all([stale, manual, fresh])
        session.commit()
        killed = auto_broadcast.expire_stale(session)
        session.refresh(stale)
        session.refresh(manual)
        session.refresh(fresh)

    assert killed == 1, f"该作废 1 条，实际 {killed}"
    assert stale.status == TaskStatus.CANCELLED
    assert manual.status == TaskStatus.QUEUED, "运营自己定时的那条不能替他撤掉"
    assert fresh.status == TaskStatus.QUEUED, "才过 1 小时的不该作废"


def test_群推送的优先级低于发布():
    """一台手机同时排着一条发布和一条群推送时，先把作品发出去。

    领任务是 `order by priority desc, created_at`，默认都是 50 —— 同分就先来先跑，
    于是一条 10:23 排的群推送会插在 10:30 的发布前面，而群发要逐个群串行点进去发，
    这台机会被占住好几分钟。作品是产能，群推送晚几分钟没关系，反过来不行。
    """
    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        moment = datetime(2026, 9, 4, 10, 59, tzinfo=CN_TZ).astimezone(timezone.utc)
        auto_broadcast.plan_for_account(session, account, moment)
        task = session.exec(
            select(PublishTask).where(PublishTask.publish_type == "group_message")
        ).first()
        default_publish_priority = PublishTask().priority
    assert task is not None
    assert task.priority < default_publish_priority, (
        f"群推送优先级 {task.priority} 不低于发布的 {default_publish_priority}，"
        "会插队占住手机"
    )


def test_账号被标记异常时手动群发也要拦住():
    """判的必须是**账号**的状态。这两张表的 health 是分开维护的 ——
    `PATCH /devices/{id}/health` 只写 DeviceAccount，所以判 Device.health 会出现
    「账号被标记异常、群发照发」。发布那条路一直判的是账号，这里要对齐。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        account.health = "limited"
        account.health_message = "被限流了"
        session.add(account)
        session.commit()
        device_id = account.device_id

    got = TestClient(app).post("/api/v1/broadcasts", json={
        "target_device_id": device_id, "text": "试试", "groups": ["看房群0"],
        "mention_all": False,
    })
    assert got.status_code == 409, f"账号异常却放行了群发：{got.status_code} {got.text}"
    assert "limited" in got.text


def test_停止必须真的停掉今天已经排好的():
    """把每天次数置 0 只挡住**未来**的排程，今天已经进队列的群消息照发 ——
    运营点了"停止"、群里还在冒消息，这是这个功能最危险的一个洞。"""
    create_db_and_tables()
    with Session(engine) as session:
        account = _setup(session)[0]
        moment = datetime(2026, 9, 4, 14, 50, tzinfo=CN_TZ).astimezone(timezone.utc)
        auto_broadcast.plan_for_account(session, account, moment)
        # 运营自己定时的那条不能被顺手撤掉
        manual = PublishTask(
            name="群发：我自己定的", publish_type="group_message", body="手动",
            target_device_id=account.device_id, status=TaskStatus.QUEUED,
            scheduled_at=datetime.utcnow() + timedelta(hours=2),
        )
        session.add(manual)
        session.commit()

        assert auto_broadcast.pending_today(session) >= 1
        cancelled = auto_broadcast.stop_now(session)
        session.refresh(manual)
        left = session.exec(
            select(PublishTask)
            .where(PublishTask.publish_type == "group_message")
            .where(PublishTask.status == TaskStatus.QUEUED)
        ).all()
        count_after = auto_broadcast.daily_count(session)

    assert cancelled >= 1, "停止没有取消任何已排的任务"
    assert count_after == 0, "停止之后每天次数应该是 0"
    assert manual.status == TaskStatus.QUEUED, "运营自己定时的那条不能替他撤掉"
    assert all(t.name.startswith("群发：") for t in left), (
        f"还剩下没取消的自动群推送：{[t.name for t in left]}"
    )


def test_预览说的和真正排出来的是同一条():
    """预览和排程共用一套种子，所以「界面上说 10 点推 A」和「群里冒出 A」必须一致。

    这条测试守的是一个**没有征兆**的故障：两边各写一份挑选逻辑，哪天改了其中一个，
    预览开始骗人而任何监控都不会响 —— 运营照着预览确认过的话，第二天才发现推错。
    """
    create_db_and_tables()
    with Session(engine) as session:
        accounts = _setup(session, count=2, mode="same", accounts=2)
        now = datetime(2026, 9, 4, 9, 0, tzinfo=CN_TZ).astimezone(timezone.utc)

        before = auto_broadcast.preview_today(session, now)
        assert before, "开了自动推送就该有预览"
        promised = {
            (row["account"], slot["time"]): slot["text"]
            for row in before for slot in row["times"]
        }
        assert all(
            s["state"] in ("upcoming", "due") for r in before for s in r["times"]
        )

        # 15:30 排一遍：14-15 那一档刚过去不到 2 小时，会被排出来；
        # 10-11 那一档过去太久，按设计跳过（发晚了的群消息是打扰）
        plan_at = datetime(2026, 9, 4, 15, 30, tzinfo=CN_TZ).astimezone(timezone.utc)
        for account in accounts:
            auto_broadcast.plan_for_account(session, account, plan_at)

        after = auto_broadcast.preview_today(session, plan_at)
        landed = 0
        for row in after:
            for slot in row["times"]:
                if slot["task_id"] is None:
                    continue
                landed += 1
                assert slot["text"] == promised[(row["account"], slot["time"])], (
                    "预览说的和真正排出来的不是同一条"
                )
        assert landed, "这个时点应该至少排出来一条"
        assert all(
            slot["text"] == promised[(row["account"], slot["time"])]
            for row in after for slot in row["times"]
        ), "还没排到的那些，预览前后也必须说同一条"


def test_预览带上还有几个群和几条可用消息():
    """开着自动推送、但一个群都没有或库里没消息 —— 这两种情况每天静静地推 0 条，
    界面上和"推成功了"长得一模一样。预览里必须能看出来。"""
    create_db_and_tables()
    with Session(engine) as session:
        _setup(session, count=1, accounts=1)
        rows = auto_broadcast.preview_today(session)
        assert rows[0]["groups"] == 1
        assert rows[0]["usable_messages"] == 2

        for message in session.exec(select(BroadcastMessage)).all():
            message.enabled = False
            session.add(message)
        session.commit()
        rows = auto_broadcast.preview_today(session)
        assert rows[0]["usable_messages"] == 0
        assert all(not slot["text"] for slot in rows[0]["times"])


def test_排程会跳过的号_预览要说明白():
    """账号异常 / 没群 / 没消息 —— 这三种情况每天静静地推 0 条，
    没有失败任务也没有告警，界面上和「推成功了」长得一模一样。

    如果预览照样给它配上时刻和正文，这一页唯一的价值（今天会发生什么）就没了。
    """
    create_db_and_tables()
    with Session(engine) as session:
        accounts = _setup(session, count=2, accounts=1)
        assert auto_broadcast.preview_today(session)[0]["blocked"] is None

        accounts[0].health = "banned"
        session.add(accounts[0])
        session.commit()
        row = auto_broadcast.preview_today(session)[0]
        assert row["blocked"] and "跳过" in row["blocked"]
        # 排程确实会跳过它 —— 预览说的和排程做的是一回事
        assert auto_broadcast.plan_for_account(session, accounts[0], _evening()) == 0

        accounts[0].health = "normal"
        session.add(accounts[0])
        for g in session.exec(select(ChatGroup)).all():
            session.delete(g)
        session.commit()
        assert "没读到群" in auto_broadcast.preview_today(session)[0]["blocked"]


def test_过了补发窗口的时刻标成错过_不能显示成待推():
    """排程对「过点超过 2 小时」直接跳过（群消息发晚了是打扰）。
    预览把它标成「待推」的话，运营会一直等一条永远不会来的消息。"""
    create_db_and_tables()
    with Session(engine) as session:
        _setup(session, count=2, windows="10-11,14-15", accounts=1)
        row = auto_broadcast.preview_today(session, _evening())[0]
        assert [s["state"] for s in row["times"]] == ["missed", "missed"]
        assert auto_broadcast.upcoming_today(session, _evening()) == 0


def test_改了时间段之后_队列里的旧任务不会从预览里蒸发():
    """旧任务的时刻不在新计划里，按时刻索引会被直接丢掉 —— 但它**照发**。
    中午改一次时间段，上午排好还没发的消息就从页面上消失了。"""
    create_db_and_tables()
    with Session(engine) as session:
        accounts = _setup(session, count=1, windows="10-11", accounts=1)
        plan_at = datetime(2026, 9, 4, 11, 30, tzinfo=CN_TZ).astimezone(timezone.utc)
        assert auto_broadcast.plan_for_account(session, accounts[0], plan_at) == 1

        settings_store.put(session, auto_broadcast.WINDOWS_KEY, "16-17")
        row = auto_broadcast.preview_today(session, plan_at)[0]
        orphans = [s for s in row["times"] if s["orphan"]]
        assert len(orphans) == 1, "改了时间段，旧任务必须还看得见"
        assert orphans[0]["state"] == "queued" and orphans[0]["task_id"]


def test_试算走的是同一套算法():
    """改了设置还没保存时要能先看会变成什么样。

    试算**只换设置、不换算法** —— 在路由层另写一遍取值逻辑的话，试算和实际
    就会各说各的，而"看到的就是会发生的"正是这一页存在的全部意义。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _setup(session, count=2, windows="10-11,14-15", accounts=1)
        trial = auto_broadcast.preview_envelope(
            session, override={"daily_count": 3, "windows": "20-21", "mode": "same"}
        )
        assert trial["dry_run"] and trial["daily_count"] == 3
        assert [s["time"][:2] for s in trial["rows"][0]["times"]] == ["20", "20", "20"]

        # 真把这套设置存下去，算出来的时刻必须一模一样
        settings_store.put(session, auto_broadcast.COUNT_KEY, "3")
        settings_store.put(session, auto_broadcast.WINDOWS_KEY, "20-21")
        real = auto_broadcast.preview_envelope(session)
        assert not real["dry_run"]
        assert [s["time"] for s in real["rows"][0]["times"]] == [
            s["time"] for s in trial["rows"][0]["times"]
        ]


def test_封套里的汇总不用前端自己遍历():
    create_db_and_tables()
    with Session(engine) as session:
        _setup(session, count=2, accounts=2)
        env = auto_broadcast.preview_envelope(session, _evening())
        assert env["summary"]["accounts"] == 2
        assert env["summary"]["planned"] == 4
        # 傍晚看：两个窗口都过了补发时间，一条都不会推了
        assert env["summary"]["missed"] == 4 and env["summary"]["upcoming"] == 0


def test_已排和待排给的字段一模一样():
    """两支字段集不一致的话，前端要写两套判断，迟早漏一处。"""
    create_db_and_tables()
    with Session(engine) as session:
        accounts = _setup(session, count=1, windows="10-11", accounts=1)
        plan_at = datetime(2026, 9, 4, 11, 30, tzinfo=CN_TZ).astimezone(timezone.utc)
        waiting = auto_broadcast.preview_today(session, plan_at)[0]["times"][0]
        auto_broadcast.plan_for_account(session, accounts[0], plan_at)
        landed = auto_broadcast.preview_today(session, plan_at)[0]["times"][0]
        assert landed["task_id"] and waiting["task_id"] is None
        assert set(landed) == set(waiting)
        # 队列里那条能点回消息库
        assert landed["message_id"] == waiting["message_id"]


def test_群列表扫错时不覆盖旧的():
    """2026-09-08 事故：11:05 的一次扫描在两台机上都读成了「某个私聊」，
    把正确的群名整个覆盖掉，当天晚班三条群发全部「未找到群」——
    而运营看到的是「群明明在，怎么会找不到」。

    正确的群名是翻**历史成功任务**才捞回来的，差一点永久丢失。

    这里只拦最坏的一种：新旧**完全不相交**。改名/加群/退群会让两批部分不同，
    那是正常的，照常覆盖。
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.entities import ChatGroup, Device
    from app.services.device_auth import hash_device_token

    with TestClient(app) as client:
        with Session(engine) as session:
            dev = Device(
                device_code=f"gs-{uuid.uuid4().hex[:8]}", name="群同步机",
                token_hash=hash_device_token("gs-token"),
            )
            session.add(dev)
            session.commit()
            session.refresh(dev)
            session.add(ChatGroup(device_id=dev.id, group_name="城市交流群B",
                                  member_count=26))
            session.commit()
            dev_id = dev.id

        H = {"X-Agent-Token": "gs-token"}
        try:
            def sync(names):
                return client.post("/api/v1/agent/groups", headers=H, json={
                    "device_id": dev_id,
                    "groups": [{"name": n, "member_count": None,
                                "can_mention_all": False} for n in names],
                })

            # ① 完全不相交 → 拦下，旧表保留
            r = sync(["某个私聊"])
            assert r.status_code == 200, r.text
            assert r.json().get("rejected") is True
            with Session(engine) as session:
                kept = [g.group_name for g in session.exec(
                    select(ChatGroup).where(ChatGroup.device_id == dev_id)).all()]
            assert kept == ["城市交流群B"], kept

            # ② 有交集（加了一个新群）→ 正常覆盖
            r = sync(["城市交流群B", "城市交流群C"])
            assert r.json()["synced"] == 2
            with Session(engine) as session:
                now = sorted(g.group_name for g in session.exec(
                    select(ChatGroup).where(ChatGroup.device_id == dev_id)).all())
            assert now == ["城市交流群B", "城市交流群C"], now

            # ③ 原来是空的 → 第一次扫描照常写入，不该被拦
            with Session(engine) as session:
                for g in session.exec(
                    select(ChatGroup).where(ChatGroup.device_id == dev_id)).all():
                    session.delete(g)
                session.commit()
            assert sync(["随便一个群"]).json()["synced"] == 1
        finally:
            with Session(engine) as session:
                for g in session.exec(
                    select(ChatGroup).where(ChatGroup.device_id == dev_id)).all():
                    session.delete(g)
                session.delete(session.get(Device, dev_id))
                session.commit()
