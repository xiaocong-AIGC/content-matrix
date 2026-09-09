"""通知系统的两条硬性质。

第二条是 blocker 级的：`enqueue` 被 `update_agent_status` 调用，而后者跑在 Agent
上报状态的 HTTP 请求线程里。如果 enqueue 出问题能污染调用方的事务，Agent 的终态
写入就会失败 → 任务永远停在 running → 永远续租 → 永不过期 → **这台机再也领不到
任务**。等于通知系统获得了停掉一台设备的能力。
"""

import json
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.enums import DeviceStatus, TaskStatus
from app.db.session import create_db_and_tables, engine
from app.models.entities import AlertDispatch, Device, NotifyRoute, PublishTask
from app.schemas.dto import AgentStatusUpdate
from app.models.entities import ContentItem, DeviceAccount, utcnow
from app.services import notify, notify_sweep, settings_store
from app.services.notify_sweep import _progress_parts
from app.services.tasks import update_agent_status


def test_enqueue_dedupes_and_never_raises():
    create_db_and_tables()
    key = "test:dedupe:1"
    with Session(engine) as session:
        for row in session.exec(
            select(AlertDispatch).where(AlertDispatch.dedup_key == key)
        ).all():
            session.delete(row)
        session.commit()

    assert notify.enqueue(event="t", dedup_key=key, title="第一条") is True
    assert notify.enqueue(event="t", dedup_key=key, title="重复的") is False

    with Session(engine) as session:
        rows = session.exec(
            select(AlertDispatch).where(AlertDispatch.dedup_key == key)
        ).all()
    assert len(rows) == 1
    assert rows[0].status == "pending"

    # 参数再离谱也不能抛（title 超长、payload 带不可序列化对象都要吞掉）
    assert notify.enqueue(
        event="t" * 200, dedup_key="k" * 500, title="x" * 999, payload={"a": object()}
    ) in (True, False)


def test_enqueue_failure_never_breaks_the_caller_transaction(monkeypatch):
    """enqueue 无条件抛异常时，Agent 的终态上报仍然必须成功落库。"""
    create_db_and_tables()
    with Session(engine) as session:
        device = session.exec(
            select(Device).where(Device.device_code == "notify-dev")
        ).first()
        if device is None:
            device = Device(device_code="notify-dev", name="notify", status=DeviceStatus.BUSY)
            session.add(device)
            session.commit()
            session.refresh(device)

        task = PublishTask(
            name="notify-task", platform="douyin",
            target_device_id=device.id, device_id=device.id,
            status=TaskStatus.RUNNING, lease_token="tok",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        def boom(**_kwargs):
            raise RuntimeError("通知系统炸了")

        monkeypatch.setattr(notify, "enqueue", boom)

        payload = AgentStatusUpdate(
            device_id=device.id, lease_token="tok",
            status=TaskStatus.WAITING_CONFIRMATION,
            step="waiting_unknown", progress=50, message="页面无法识别",
        )
        updated = update_agent_status(session, task, payload)

        assert updated.status == TaskStatus.WAITING_CONFIRMATION
        assert updated.waiting_since is not None, "转人工时刻没落库 → 人工窗口又会按开跑时间算"


def test_route_picking_prefers_exact_city_then_wildcard():
    create_db_and_tables()
    with Session(engine) as session:
        for row in session.exec(select(NotifyRoute)).all():
            session.delete(row)
        session.commit()
        session.add(NotifyRoute(city="*", channel="wecom", target="https://example/fallback"))
        session.add(NotifyRoute(city="上海", channel="wecom", target="https://example/sh"))
        session.commit()

        sh = notify._pick_routes(session, "上海", "warning")
        assert [r.target for r in sh] == ["https://example/sh"], "有精确城市时不该用兜底"

        bj = notify._pick_routes(session, "北京", "warning")
        assert [r.target for r in bj] == ["https://example/fallback"], "没配的城市要走兜底"

        # 低于渠道 min_severity 的事件不发
        session.exec(select(NotifyRoute).where(NotifyRoute.city == "上海")).first()
        route = session.exec(select(NotifyRoute).where(NotifyRoute.city == "上海")).first()
        route.min_severity = "critical"
        session.add(route)
        session.commit()
        assert notify._pick_routes(session, "上海", "warning") == []


def test_render_只排版不造句():
    """正文来自 payload 里已经写好的句子，render 只负责排版。

    以前是「字段名：值」（原因：/说明：/停留页面：），那是后台表单的样子不是通知的
    样子，而且只要有人塞个新字段进来就可能把英文 key 漏进群里。现在入队方负责把话
    说明白，render 不认识任何字段名，也就不可能漏。
    """
    row = AlertDispatch(
        dedup_key="render:1", event="human_security", severity="critical",
        city="深圳", task_id=42, title="🚨 需要有人去手机上处理　小满每日（深圳）",
        payload_json=json.dumps(
            {"lines": ["抖音弹出了安全验证，自动操作已经停下",
                       "请到 云机6012 上把验证做掉，5 分钟内没人处理这条就自动跳过",
                       "编号 #42"]},
            ensure_ascii=False,
        ),
    )
    markdown, plain = notify.render(row)
    assert "需要有人去手机上处理" in markdown and "小满每日" in markdown
    assert "安全验证" in markdown and "云机6012" in markdown and "#42" in markdown
    assert "**" not in plain, "邮件正文不该带 markdown 记号"

    # payload 里塞脏东西也不会漏进消息 —— render 只读 lines
    dirty = AlertDispatch(
        dedup_key="render:2", event="x", severity="warning", title="标题",
        payload_json=json.dumps(
            {"lines": ["一句正常的话"], "reason": "内部原因", "device_id": 7},
            ensure_ascii=False,
        ),
    )
    out, _ = notify.render(dirty)
    assert "一句正常的话" in out
    for leaked in ("reason", "device_id", "内部原因", "7"):
        assert leaked not in out, f"render 把 {leaked} 漏进消息了"


def test_通知里没有英文字段名也没有假城市():
    """群消息里不能出现英文字段名，也不能把「未分组」当成城市播出去。

    「未分组」是 DeviceAccount.city 的默认值，含义是"这个号漏配了城市"。
    播出去会让人去找一个不存在的城市。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        device = _account(session, "nofield", "未分组", "没城市的号")
        task = PublishTask(
            name="t", platform="douyin", publish_type="text",
            publish_title="深圳周末去哪玩",
            target_device_id=device.id, device_id=device.id,
            status=TaskStatus.FAILED, current_step="unknown_page",
            error_message="卡在需要人工确认的页面，没人处理，已自动跳过",
            finished_at=utcnow(),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        from app.services.tasks import notify_task_finished

        notify_task_finished(session, task)

    with Session(engine) as session:
        row = session.exec(
            select(AlertDispatch).where(AlertDispatch.dedup_key == f"taskdone:{task.id}")
        ).first()
    assert row is not None
    markdown, _ = notify.render(row)
    for leaked in ("account", "device", "step", "reason", "detail", "payload", "city"):
        assert leaked not in markdown, f"消息里漏了英文字段名 {leaked}"
    assert "未分组" not in markdown, "「未分组」不是城市，不该出现在群消息里"
    assert "深圳周末去哪玩" in markdown, "失败消息要能看出是哪一篇"
    assert "认不出来" in markdown, "要说清楚卡在哪个页面（说人话的那种）"


def test_every_finished_task_is_announced():
    """用户口径：账号不多，**每一条发布都要播**，成功也播。

    只播失败的话，群里安静时运营分不清"今天一切正常"和"系统死了"——
    而"不用打开控制台"正是这套通知存在的理由。
    """
    create_db_and_tables()
    with Session(engine) as session:
        device = session.exec(
            select(Device).where(Device.device_code == "announce-dev")
        ).first()
        if device is None:
            device = Device(device_code="announce-dev", name="云机6099",
                            status=DeviceStatus.BUSY)
            session.add(device)
            session.commit()
            session.refresh(device)
        if not session.exec(
            select(DeviceAccount).where(DeviceAccount.device_id == device.id)
        ).first():
            session.add(DeviceAccount(device_id=device.id, platform="douyin",
                                      nickname="播报测试号", city="深圳"))
            session.commit()

        task = PublishTask(
            name="announce-task", platform="douyin", publish_type="text",
            publish_title="深圳周末去哪玩",
            target_device_id=device.id, device_id=device.id,
            status=TaskStatus.RUNNING, lease_token="tok-announce",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        update_agent_status(session, task, AgentStatusUpdate(
            device_id=device.id, lease_token="tok-announce",
            status=TaskStatus.SUCCEEDED, step="done", progress=100, message="发布完成",
        ))

    with Session(engine) as session:
        row = session.exec(
            select(AlertDispatch).where(AlertDispatch.dedup_key == f"taskdone:{task.id}")
        ).first()
    assert row is not None, "发布成功没有播报 —— 运营看不到今天发了什么"
    assert row.event == "publish_ok" and row.severity == "info"
    markdown, _ = notify.render(row)
    assert "播报测试号" in markdown and "深圳" in markdown
    assert "深圳周末去哪玩" in markdown, "成功消息里要能看出发的是哪篇"


def test_content_pool_never_invents_a_city_for_unset_accounts():
    """没设城市的号不该造出一个「未分组」内容池（它永远 0 篇 → 天天假告急）。"""
    create_db_and_tables()
    with Session(engine) as session:
        for row in session.exec(select(AlertDispatch)).all():
            session.delete(row)
        for account in session.exec(select(DeviceAccount)).all():
            session.delete(account)
        session.commit()
        _account(session, "nocity-dev", "未分组", "没设城市的号")

        notify_sweep._scan_content_pool(session, notify_sweep._cn_now())

    with Session(engine) as session:
        alerts = session.exec(select(AlertDispatch)).all()
    events = {a.event for a in alerts}
    assert not any(a.city == "未分组" for a in alerts), "凭空造出了「未分组」这个城市"
    assert "content_pool_critical" not in events and "content_pool_low" not in events
    assert "account_no_city" in events, "该提醒的是「这个号漏配城市」，不是「内容不够」"


def _reset_accounts(session):
    for account in session.exec(select(DeviceAccount)).all():
        session.delete(account)
    session.commit()


def _account(session, code, city, nickname, *, active=True):
    """建一个号。

    `active=True` 会顺手补一条 48 小时内的成功发布 —— 通知只认「最近 48 小时发过」
    的号（用户定的：超过就是走别的方式在发，不归这套系统管）。不补的话这个号
    在所有摘要里都不存在。
    """
    device = session.exec(select(Device).where(Device.device_code == code)).first()
    if device is None:
        device = Device(device_code=code, name=f"云机-{code}")
        session.add(device)
        session.commit()
        session.refresh(device)
    session.add(DeviceAccount(device_id=device.id, platform="douyin",
                              nickname=nickname, city=city))
    if active:
        session.add(PublishTask(
            name=f"seed-{code}", platform="douyin", publish_type="text",
            target_device_id=device.id, device_id=device.id,
            status=TaskStatus.SUCCEEDED,
            finished_at=utcnow() - timedelta(hours=20),   # 昨天发过 → 算活跃
        ))
    session.commit()
    return device


def test_每个城市单独占一行():
    """用户明确要求：「不同城市要隔开，不能放一起，不同城市单独用一行。」

    旧版用 ｜ 把城市拼在同一行，在企微的窄卡片里被折成一坨，折行点还落在名字中间。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        for code, city, nick in (("lay-sh", "上海", "沪上小王"),
                                 ("lay-bj", "北京", "京城小李"),
                                 ("lay-sz", "深圳", "鹏城小张")):
            _account(session, code, city, nick)
        lines = _progress_parts(session)["city_lines"]

    for city in ("上海", "北京", "深圳"):
        owners = [ln for ln in lines if city in ln]
        assert len(owners) == 1, f"{city} 没有独占一行：{lines}"
    for line in lines:
        hits = [c for c in ("上海", "北京", "深圳") if c in line]
        assert len(hits) <= 1, f"这一行挤了多个城市：{line}"


def test_没设城市的号不进城市块():
    """「未分组」是 DeviceAccount.city 的默认值，含义是"这个号漏配了城市"。

    把它排成第四个城市行（和北京并列、同样的图标和分数）会让运营以为系统里真有个
    叫「未设城市」的城市要运营。它得单独说，但又不能直接删——它占着目标里的 1 个号。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        _account(session, "nc-bj", "北京", "京城小李")
        _account(session, "nc-none", "未分组", "没城市的号")
        parts = _progress_parts(session)
        order = notify_sweep._city_order(session)

    assert "未设城市" not in order and "未分组" not in order, f"城市序里混进了假城市：{order}"
    for line in parts["city_lines"]:
        assert "未设城市" not in line and "未分组" not in line, f"城市块里出现了假城市：{line}"
    assert parts["goal"] == 2, "没设城市的号仍然要算进今天的目标，不能凭空少一个"
    assert any("没城市的号" in a for a in parts["actions"]), "该单独提醒去补城市配置"


def test_城市顺序可配置且稳定():
    """顺序必须是死的。按当前账号数现算的话，号一增减城市就换位置，运营就永远形不成
    「我负责的那行在第几行」的肌肉记忆 —— 而这条消息是他们唯一的界面。"""
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        for i in range(3):
            _account(session, f"ord-gz{i}", "广州", f"广州号{i}")
        _account(session, "ord-xm", "厦门", "厦门号")
        assert notify_sweep._city_order(session) == ["广州", "厦门"], "默认按号数倒序"

        settings_store.put(session, notify_sweep.CITY_ORDER_KEY, "厦门,广州")
        assert notify_sweep._city_order(session) == ["厦门", "广州"], "配置序必须压过号数"
        settings_store.put(session, notify_sweep.CITY_ORDER_KEY, "")


def test_城市行按号算不按篇算():
    """分子数「篇」、分母数「号」是会当场骗人的错：一个号发两篇就渲染成
    「北京 2/2 全发完了」，而实际另一个号一篇没发。"""
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        busy = _account(session, "cnt-a", "成都", "勤快号")
        _account(session, "cnt-b", "成都", "偷懒号")
        for n in range(2):                      # 同一个号发两篇
            session.add(PublishTask(
                name=f"double-{n}", platform="douyin", publish_type="text",
                target_device_id=busy.id, device_id=busy.id,
                status=TaskStatus.SUCCEEDED, finished_at=utcnow(),
            ))
        session.commit()
        city_lines = _progress_parts(session)["city_lines"]
        line = [ln for ln in city_lines if "成都" in ln][0]

    assert "2 个号发了 1 个" in line, f"一个号发两篇被算成了两个号：{line}"
    # 没发的那个号必须**自己占一行** —— 折进城市行里运营就分辨不出是哪个号
    assert "> 偷懒号" in city_lines, f"没发的号没有单独一行：{city_lines}"
    assert not any("勤快号" in ln for ln in city_lines), "已经发了的号不该再念一遍"


def test_日报不把今天的完成度渲染成一屏红():
    """日报 09:00 发 —— 那个时刻「今天」必然是 0/N，把完成度块拼进去等于每天早上
    发一次假警报（其实只是还没到发布时间）。一周之内这条消息就被无视了，
    而它是运营一天里唯一一定会看的一条。日报的「今天」只能讲计划和弹药。"""
    create_db_and_tables()
    sent = []
    with Session(engine) as session:
        _reset_accounts(session)
        for i in range(3):
            _account(session, f"dg-{i}", "杭州", f"杭州号{i}")
        settings_store.put(session, notify_sweep._LAST_DAILY_KEY, "19700101")
        original = notify_sweep._send_to_all_routes
        notify_sweep._send_to_all_routes = lambda _s, md: sent.append(md)
        try:
            notify_sweep._daily_digest(
                session, datetime.now(timezone.utc).replace(hour=2)  # 中国时间 10:00
            )
        finally:
            notify_sweep._send_to_all_routes = original

    assert sent, "日报没发出去"
    body = sent[0]
    # 只查「今天」那一段 —— 昨天段出现 0/3 是正常的（昨天确实一个号没发）
    today = body.split("**今天", 1)[1].split("**要动手的**", 1)[0]
    assert "0/3" not in today, f"日报把今天的 0 完成度渲染出来了：{today}"
    assert "全城一条没发" not in today, f"早上 9 点报「全城没发」是假警报：{today}"
    assert "🔴" not in today, f"每天早上一屏假红：{today}"
    assert "**今天要处理**" in body, "日报必须永远有这一段，没事也要明确写没事"


def test_日报按它汇报的那天命名():
    """用户原话：「为什么日报还有昨日的内容？要么就写好几月几日日报」。
    一条 09-04 发出、标题写 09-04、身体却全是 09-03 数据的消息，谁看都别扭。"""
    create_db_and_tables()
    sent = []
    with Session(engine) as session:
        _reset_accounts(session)
        _account(session, "name-1", "南京", "南京号")
        settings_store.put(session, notify_sweep._LAST_DAILY_KEY, "19700101")
        original = notify_sweep._send_to_all_routes
        notify_sweep._send_to_all_routes = lambda _s, md: sent.append(md)
        try:
            now = datetime.now(timezone.utc).replace(hour=2)
            notify_sweep._daily_digest(session, now)
        finally:
            notify_sweep._send_to_all_routes = original

    cn_yesterday = (
        datetime.now(timezone.utc).astimezone(notify_sweep._CN_TZ) - timedelta(days=1)
    )
    title = sent[0].splitlines()[0]
    cn_today = datetime.now(timezone.utc).astimezone(notify_sweep._CN_TZ)
    assert f"{cn_yesterday:%m-%d}" in title, (
        f"标题该按它汇报的那一天命名，而不是发送日：{title}"
    )
    assert f"{cn_today:%m-%d}" not in title, f"标题不该写发送日：{title}"
    # 一条消息横跨两天，必须用带日期的小标题把两天物理切开 ——
    # 结构本身说清楚了，就不需要再写一句"上半段是昨天下半段是今天"的旁白
    assert "昨日汇总" in title, f"标题要点明这是昨天的汇总：{title}"
    assert f"今天 {cn_today:%m-%d}" in sent[0], "今天那段要带上今天的日期"


def test_停留页面映射表和_Agent_实际发的_step_对得上():
    """这张表的键必须和 Agent 真正上报的字符串一一对得上。

    之前有 5 个键是凭印象写的（waiting_unknown / waiting_security / open_app /
    publish / waiting_login），Agent 根本不发这些值，于是群消息里「停留页面」那一行
    永远是空的 —— 一张对不上的映射表和没有映射是一回事，而且不会报错，只会一直沉默。

    真实来源：android-agent 的 TaskCoordinator 里 reporter.status(...) 的第二个参数。
    这里把它抄成常量盯着；改 Agent 的 step 名字时这条用例会红。
    """
    from app.services.tasks import AGENT_STEPS, _MUTE_STEPS, _STEP_CN

    # 抄自 android-agent/.../execution/TaskCoordinator.kt 的 reporter.status 第二参
    reported_by_agent = {
        "claimed", "launching_douyin", "opening_publish_entry",
        "selecting_publish_type", "composing_text", "selecting_template",
        "selecting_media", "security_challenge", "unknown_page",
        "publishing", "completed",
    }
    assert set(AGENT_STEPS) == reported_by_agent, (
        "映射表和 Agent 实际上报的 step 对不上："
        f"表里多了 {set(AGENT_STEPS) - reported_by_agent}，"
        f"漏了 {reported_by_agent - set(AGENT_STEPS)}"
    )
    for step in reported_by_agent - _MUTE_STEPS:
        assert step in _STEP_CN, f"{step} 没有中文说法，群里会是空的"
        assert _STEP_CN[step].isascii() is False, f"{step} 的中文说法写成英文了"


def test_安全验证会升级成严重并触发_at_值班():
    """安全验证必须是 critical —— 它是唯一一个不马上有人处理就会掉登录态的场景。

    升级判据是 `"security" in step`，而 Agent 发的是 "security_challenge"。
    哪天有人把 Agent 的 step 改成别的名字，这条静默失效：告警会降级成 warning、
    不再 @ 值班，而线上表现只是"没人来处理"，查不到任何报错。
    """
    create_db_and_tables()
    with Session(engine) as session:
        device = session.exec(
            select(Device).where(Device.device_code == "sec-dev")
        ).first()
        if device is None:
            device = Device(device_code="sec-dev", name="云机6012",
                            status=DeviceStatus.BUSY)
            session.add(device)
            session.commit()
            session.refresh(device)
        task = PublishTask(
            name="sec-task", platform="douyin", publish_type="text",
            target_device_id=device.id, device_id=device.id,
            status=TaskStatus.RUNNING, lease_token="tok-sec",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        update_agent_status(session, task, AgentStatusUpdate(
            device_id=device.id, lease_token="tok-sec",
            status=TaskStatus.WAITING_CONFIRMATION,
            step="security_challenge", progress=30,
            message="检测到安全验证，已停止自动操作，请人工处理",
        ))

    with Session(engine) as session:
        row = session.exec(
            select(AlertDispatch)
            .where(AlertDispatch.dedup_key == f"human:{task.id}:security_challenge")
        ).first()
    assert row is not None, "安全验证没有入队告警"
    assert row.severity == "critical", "安全验证必须是 critical，否则不会 @ 值班"
    markdown, _ = notify.render(row)
    assert "安全验证" in markdown, "要说清楚是安全验证（照抄手机上的字）"
    assert "云机6012" in markdown, "要说清楚去哪台手机上处理"


def test_坏掉的设置值不能停掉整个通知():
    """一行设置值不该有能力停掉后台 loop —— 但它有过。

    用远程 cmd 控制台（GBK）往 appsetting 写中文，落进去就是非 UTF-8 字节，
    之后 sqlite3 驱动在 fetch 阶段抛 OperationalError，而 sweep 每段都吞异常，
    表现就是「通知毫无征兆地停了」：2 小时播报和日报一起哑掉，不报错不留痕。
    线上真的这么停过一次（notify:city_order 被写成 GBK）。
    """
    import sqlite3

    from app.core.config import get_settings as _cfg

    create_db_and_tables()
    path = _cfg().database_url.replace("sqlite:///", "")
    # 复现线上那次的真实形状：列类型是 TEXT，字节却不是 UTF-8（GBK 控制台写的）。
    # 用 CAST(x'..' AS TEXT) 才能造出这种行；直接绑定 bytes 会存成 BLOB，是另一回事。
    gbk = "北京,上海".encode("gbk").hex().upper()
    raw = sqlite3.connect(path)
    raw.execute(
        f"insert into appsetting(key, value, updated_at) "
        f"values(?, CAST(x'{gbk}' AS TEXT), datetime('now')) "
        f"on conflict(key) do update set value=excluded.value",
        (notify_sweep.CITY_ORDER_KEY,),
    )
    raw.commit()
    raw.close()

    with Session(engine) as session:
        # 读不出来要退回默认值，不能抛
        assert settings_store.get(session, notify_sweep.CITY_ORDER_KEY, "") == ""
        # 整条链路照样能出消息
        parts = _progress_parts(session)
        assert "headline" in parts

    with Session(engine) as session:
        settings_store.put(session, notify_sweep.CITY_ORDER_KEY, "")


def test_日报的本周累计跟着天数往前走():
    """日报里那行「本周累计 N 篇（X 起）」的起点必须是**最近一个周五**。

    `_week_window()` 返回的是周报的窗口（上一个走完的周：上周五→本周四）。日报原来
    直接解包它，于是周六到周四这六天都显示上周的数字、一动不动 —— 看着像统计卡死，
    实际是窗口取错了，而它偏偏是「让周五的周报不是突然袭击」这行字唯一的作用。

    这条用例直接跑 _daily_digest 拿真消息，不去测 _week_window 的契约 ——
    契约一直是对的，错的是调用方。
    """
    import re

    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        _account(session, "wtd-1", "苏州", "苏州号")
        original = notify_sweep._send_to_all_routes
        for offset, weekday_name in ((4, "周二"), (6, "周四")):
            sent = []
            # 2026-09-04 是周五；+4 → 周二，+6 → 周四
            day = datetime(2026, 9, 4, 10, 0, tzinfo=notify_sweep._CN_TZ) + timedelta(
                days=offset
            )
            settings_store.put(session, notify_sweep._LAST_DAILY_KEY, "19700101")
            notify_sweep._send_to_all_routes = lambda _s, md: sent.append(md)
            try:
                notify_sweep._daily_digest(session, day.astimezone(timezone.utc))
            finally:
                notify_sweep._send_to_all_routes = original

            assert sent, f"{weekday_name}的日报没发出去"
            found = re.search(r"本周已发 .*?（(\d\d)-(\d\d) 起）", sent[0])
            assert found, f"{weekday_name}的日报里没有「本周已发」那行：{sent[0]}"
            begin = datetime(
                day.year, int(found.group(1)), int(found.group(2)),
                tzinfo=notify_sweep._CN_TZ,
            )
            gap = (day.date() - begin.date()).days
            assert 0 <= gap < 7, (
                f"{weekday_name}（{day:%m-%d}）说「本周已发…从 {begin:%m-%d} 起」，"
                f"隔了 {gap} 天 —— 取到上一周去了"
            )
            assert begin.weekday() == notify_sweep.WEEKLY_WEEKDAY, "本周起点必须是周五"


def test_拼消息出错不会把当天的日报永久跳过():
    """三个摘要都要「先拼好、再盖戳、最后发」。

    sweep 每段都吞异常，所以拼消息时任何一次抛错都会变成：戳已经盖上了 →
    这一整天的日报直接没有，而且完全静默。今天真踩到过一次（一行 GBK 设置值
    让 _city_order 抛错，2 小时播报和日报一起哑掉）。拼装出错就该等下一轮再试。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        _account(session, "stamp-1", "宁波", "宁波号")
        settings_store.put(session, notify_sweep._LAST_DAILY_KEY, "19700101")

        original = notify_sweep._stock_lines
        notify_sweep._stock_lines = lambda _s: (_ for _ in ()).throw(RuntimeError("取数炸了"))
        try:
            notify_sweep._daily_digest(
                session, datetime.now(timezone.utc).replace(hour=2)
            )
        except RuntimeError:
            pass          # sweep 在真实环境里会吞掉它
        finally:
            notify_sweep._stock_lines = original

        stamp = settings_store.get(session, notify_sweep._LAST_DAILY_KEY, "")
    assert stamp == "19700101", (
        f"拼消息炸了却已经盖了戳（{stamp}）—— 这一整天的日报就这么永久跳过了"
    )


def test_城市顺序可以走接口改而不必碰库():
    """这个值以前只能手工改库，而手工改库正是把它写坏的那条路：远程 cmd 控制台是
    GBK，写进去的中文不是 UTF-8，之后每次读都抛解码错、通知整个哑掉且不报错。
    接口收发一律 UTF-8，把这条路堵上。"""
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        _account(session, "api-bj", "北京", "京号")
        _account(session, "api-sh", "上海", "沪号")
        settings_store.put(session, notify_sweep.CITY_ORDER_KEY, "")

    client = TestClient(app)
    got = client.put("/api/v1/notifications/city-order", json={"cities": ["上海", "北京"]})
    assert got.status_code == 200, got.text
    assert got.json()["effective"] == ["上海", "北京"]

    # 存进去的必须是干净的 UTF-8，读回来一模一样
    with Session(engine) as session:
        assert settings_store.get(session, notify_sweep.CITY_ORDER_KEY, "") == "上海,北京"

    # 空数组 = 恢复默认（按在用账号数）
    back = client.put("/api/v1/notifications/city-order", json={"cities": []})
    assert back.status_code == 200
    assert set(back.json()["effective"]) == {"上海", "北京"}

    assert client.put("/api/v1/notifications/city-order", json={"cities": "北京"}).status_code == 400


def test_没发的号一个一行不许折进城市行():
    """用户原话：「账号肯定是单独的一行啊，这样好分辨，我让你优化描述，没让你优化维度」。

    上一轮为了让消息短，把账号名折进了城市那一行，装不下就干脆不列（「一个都没发」）。
    那是**砍维度**不是改文案：运营看到「北京 8 个号一个都没发」根本不知道是哪 8 个，
    还得去开控制台 —— 而"不用开控制台"正是这套通知存在的全部理由。

    发完的城市反过来压成一行、不列名字：成功的号单条播报已经逐条报过了。
    于是消息随着一天推进越来越短，异常自然浮到最上面。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        for i in range(4):
            _account(session, f"row-bj{i}", "北京", f"北京号{i}")
        done = _account(session, "row-sh", "上海", "上海号")
        session.add(PublishTask(
            name="done", platform="douyin", publish_type="text",
            target_device_id=done.id, device_id=done.id,
            status=TaskStatus.SUCCEEDED, finished_at=utcnow(),
        ))
        session.commit()
        lines = _progress_parts(session)["city_lines"]

    for i in range(4):
        assert f"> 北京号{i}" in lines, f"北京号{i} 没有单独一行：{lines}"
    # 上海全发完了 → 压成一行，不列名字
    shanghai = [ln for ln in lines if "上海" in ln]
    assert len(shanghai) == 1 and "都发完了" in shanghai[0], f"发完的城市该压成一行：{shanghai}"
    assert not any("上海号" in ln for ln in lines), "已经发完的号不该再被念一遍"


def test_只用企微渲染得出来的emoji():
    """🟡🟢🟠 是 Unicode 12（2019）的方块 emoji，用户的企微客户端渲染成 ▯▯（有截图）。

    群里出现豆腐块比没有图标更糟 —— 它看起来像消息坏了。只用确认能渲染的那几个。
    """
    import io as _io
    from pathlib import Path

    unsupported = ["🟡", "🟢", "🟠", "🟣", "🟤", "🔵", "🕳", "⏸"]
    root = Path(__file__).resolve().parent.parent
    for rel in ("app/services/notify_sweep.py", "app/services/notify.py",
                "app/services/tasks.py"):
        text = _io.open(root / rel, encoding="utf-8").read()
        for glyph in unsupported:
            assert glyph not in text, (
                f"{rel} 里用了 {glyph} —— 这个 emoji 在用户的企微里是个方块"
            )


def test_超过48小时没发的号不再提醒():
    """用户原话：「只有当天没发的号（也就是 48 小时内）才做提醒，如果超过该时间没发过
    的号，不需要提醒，肯定是通过其他方式来发布了。」

    线上确实如此：22 个在用号里有 8 个最后一次发布是 11~15 天前甚至从没发过，而通知
    每两小时把这 8 个名字念一遍。它们不归这套系统驱动，念了只是噪音 —— 而噪音会让
    整个群被忽略。分母也要跟着变，否则达成率永远是错的。
    """
    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        live = _account(session, "act-live", "杭州", "还在发的号")
        _account(session, "act-gone", "杭州", "早就不发的号", active=False)
        # 一个 5 天前发过、之后再没发的号 —— 同样不该再提醒
        stale = _account(session, "act-stale", "杭州", "五天前发过的号", active=False)
        session.add(PublishTask(
            name="old", platform="douyin", publish_type="text",
            target_device_id=stale.id, device_id=stale.id,
            status=TaskStatus.SUCCEEDED, finished_at=utcnow() - timedelta(days=5),
        ))
        session.commit()

        roster = [a.nickname for a in notify_sweep._active_accounts(session)]
        parts = _progress_parts(session)

    assert roster == ["还在发的号"], f"名册应该只剩最近 48 小时发过的：{roster}"
    assert parts["goal"] == 1, f"分母也要跟着只算活跃号，实际 {parts['goal']}"
    body = "\n".join(parts["city_lines"] + parts["actions"])
    for gone in ("早就不发的号", "五天前发过的号"):
        assert gone not in body, f"{gone} 超过 48 小时没发了，不该再出现：{body}"


def test_全部停摆时必须喊出来而不是安静():
    """反面：如果**所有**号都超过 48 小时没发，活跃名册会变空。

    这时候"没有活跃号所以不用发消息"是最危险的结论 —— 那恰恰是整套系统停了的样子，
    而运营正是靠「群里没动静 = 没事」来决定不打开控制台的。**沉默永远不能表示故障。**
    """
    create_db_and_tables()
    sent = []
    with Session(engine) as session:
        _reset_accounts(session)
        for i in range(3):
            _account(session, f"dead-{i}", "武汉", f"停摆号{i}", active=False)
        settings_store.put(session, notify_sweep._LAST_ACTIVITY_KEY, "")
        original = notify_sweep._send_to_all_routes
        notify_sweep._send_to_all_routes = lambda _s, md: sent.append(md)
        try:
            notify_sweep._activity_digest(
                session, datetime.now(timezone.utc).replace(hour=3)  # 中国时间 11:00
            )
        finally:
            notify_sweep._send_to_all_routes = original

    assert sent, "全部停摆的时候通知反而安静了 —— 这是最危险的失败方式"
    assert "48" in sent[0] and "一个号都没发过" in sent[0], sent[0]
    assert "3 个号" in sent[0], f"要说清楚有几个号停了：{sent[0]}"


def test_在跑的号只有一个定义():
    """首页 KPI 和企微群通知必须用同一份口径，否则运营两边看到不同的数。

    线上真出现过：群里说「14 个号发了 7 个」、首页说「7 / 44」。首页对全部 37 台
    设备的所有账号求和，还把 daily_quota（上限 2）当成了目标。
    而审计当时建议的另一个口径（已登录 + auto_publish）在线上会得到 **0** ——
    37 个账号那个开关全是 false。所以定义必须收在后端一处，前端只负责显示。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    create_db_and_tables()
    with Session(engine) as session:
        _reset_accounts(session)
        _account(session, "roster-a", "武汉", "在跑的号A")
        _account(session, "roster-b", "武汉", "在跑的号B")
        _account(session, "roster-c", "武汉", "早就不发了", active=False)
        expected = [a.id for a in notify_sweep._active_accounts(session)]

    got = TestClient(app).get("/api/v1/devices/roster")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["in_service"] == 2, f"在跑的号数不对：{body}"
    assert sorted(body["in_service_ids"]) == sorted(expected), (
        "接口给的名册和通知用的不是同一批 —— 首页和群消息又会对不上"
    )
    assert body["today_target"] == 2, f"今日目标该是 2 个号 × 1 篇：{body}"
