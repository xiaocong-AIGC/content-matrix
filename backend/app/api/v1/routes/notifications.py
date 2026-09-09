"""发布结果通知中心 — derived from terminal publish tasks (success/fail).

No separate table: a notification IS the terminal state of a publish task, so
the cross-post fan-out naturally produces one notification per platform. Unread
state is tracked client-side (last-seen timestamp), so this stays a pure read.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.core.enums import TaskStatus
from app.core.serialization import normalize_datetimes
from app.db.session import get_session
from app.models.entities import Device, DeviceAccount, PublishTask

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(limit: int = 40, session: Session = Depends(get_session)):
    """Most recent publish RESULTS (succeeded / failed), newest first — the bell
    feed. A `both` content fans out to 抖音 + 小红书, so it yields one result each."""
    limit = max(1, min(limit, 200))
    tasks = session.exec(
        select(PublishTask)
        .where(PublishTask.status.in_([TaskStatus.SUCCEEDED, TaskStatus.FAILED]))
        .where(PublishTask.finished_at.is_not(None))
        .order_by(PublishTask.finished_at.desc())
        .limit(limit)
    ).all()
    devices = {d.id: d for d in session.exec(select(Device)).all()}
    accounts = {
        (a.device_id, a.platform): a
        for a in session.exec(select(DeviceAccount)).all()
    }
    # content_id → 这条内容涉及的**不同平台**集合。
    # ⚠ 以前这里数的是"同 content_id 下有几条任务"，>1 就判跨平台。但 retry_task
    # 是「新建一条任务 + 保留原任务作为历史」，两条共用同一个 content_id —— 于是
    # **任何一条内容只要重发过一次，就会被打上「跨平台」标签**，哪怕两条都是抖音。
    # 真正的跨平台是 platform='both' 扇出成 抖音 + 小红书，所以要数不同平台数。
    fanout: dict[int, set[str]] = {}
    for t in tasks:
        if t.content_id:
            fanout.setdefault(t.content_id, set()).add(t.platform)

    out = []
    for t in tasks:
        acct = accounts.get((t.device_id, t.platform))
        dev = devices.get(t.device_id)
        out.append(
            {
                "id": t.id,
                "task_id": t.id,
                "status": t.status,
                "ok": t.status == TaskStatus.SUCCEEDED,
                "platform": t.platform,
                "account": (acct.nickname if acct else None)
                or (dev.name if dev else "未知账号"),
                "device_name": dev.name if dev else None,
                "title": t.publish_title or t.cover_title or t.name,
                "kind": t.publish_type,  # text | group_message
                "is_fanout": bool(t.content_id and len(fanout.get(t.content_id, ())) > 1),
                "error": t.error_message if t.status == TaskStatus.FAILED else None,
                "finished_at": t.finished_at,
            }
        )
    return normalize_datetimes(out)


# ---------------------------------------------------------------------------
# 外发渠道（企微群 / 邮件）配置与自检
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field  # noqa: E402

from app.models.entities import AlertDispatch, NotifyRoute, utcnow  # noqa: E402
from app.services import notify  # noqa: E402


def _mask(target: str) -> str:
    """webhook key 等价于「往这个群发消息」的权限，接口里不回显全量。"""
    if len(target) <= 12:
        return target
    return f"{target[:34]}…{target[-6:]}" if "key=" in target else f"{target[:8]}…{target[-4:]}"


class NotifyRouteIn(BaseModel):
    city: str = Field(default="*", max_length=40)
    channel: str = Field(default="wecom", max_length=16)
    target: str = Field(default="", max_length=500)
    oncall_mobiles: str = Field(default="", max_length=500)
    min_severity: str = Field(default="warning", max_length=16)
    quiet_hours: str = Field(default="", max_length=32)
    daily_cap: int = 200
    enabled: bool = True


@router.get("/routes")
def list_routes(session: Session = Depends(get_session)):
    rows = session.exec(select(NotifyRoute).order_by(NotifyRoute.city)).all()
    return [
        {
            "id": r.id, "city": r.city, "channel": r.channel,
            "target": _mask(r.target), "oncall_mobiles": r.oncall_mobiles,
            "min_severity": r.min_severity, "quiet_hours": r.quiet_hours,
            "daily_cap": r.daily_cap, "enabled": r.enabled,
            "consecutive_failures": r.consecutive_failures, "last_error": r.last_error,
        }
        for r in rows
    ]


@router.post("/routes", status_code=201)
def create_route(payload: NotifyRouteIn, session: Session = Depends(get_session)):
    existing = session.exec(
        select(NotifyRoute)
        .where(NotifyRoute.city == payload.city)
        .where(NotifyRoute.channel == payload.channel)
    ).first()
    row = existing or NotifyRoute()
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    row.consecutive_failures = 0
    row.last_error = None
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "city": row.city, "channel": row.channel, "target": _mask(row.target)}


@router.delete("/routes/{route_id}", status_code=204)
def delete_route(route_id: int, session: Session = Depends(get_session)):
    row = session.get(NotifyRoute, route_id)
    if row:
        session.delete(row)
        session.commit()


@router.get("/city-order")
def get_city_order(session: Session = Depends(get_session)):
    """群消息里城市的显示顺序。

    这个值以前只能手工改库 —— 而我就是那样把它写坏的：远程 cmd 控制台是 GBK，
    写进去的中文不是 UTF-8，之后每次读都抛解码错，通知整个哑掉且不报错。
    有了这个接口就不必再碰库（FastAPI 收发一律 UTF-8）。
    """
    from app.services import settings_store
    from app.services.notify_sweep import CITY_ORDER_KEY, _city_order

    raw = settings_store.get(session, CITY_ORDER_KEY, "")
    return {
        "configured": [c.strip() for c in raw.split(",") if c.strip()],
        "effective": _city_order(session),
    }


@router.put("/city-order")
def set_city_order(payload: dict, session: Session = Depends(get_session)):
    """设置城市顺序。传 {"cities": ["北京", "上海", "深圳"]}；传空数组表示恢复默认
    （按在用账号数从多到少）。没列到的城市会排在后面，不会丢。"""
    from app.services import settings_store
    from app.services.notify_sweep import CITY_ORDER_KEY, _city_order

    cities = payload.get("cities") or []
    if not isinstance(cities, list) or any(not isinstance(c, str) for c in cities):
        raise HTTPException(status_code=400, detail="cities 必须是字符串数组")
    cleaned = [c.strip() for c in cities if c.strip() and "," not in c]
    settings_store.put(session, CITY_ORDER_KEY, ",".join(cleaned))
    return {"configured": cleaned, "effective": _city_order(session)}


@router.post("/test")
def send_test(city: str = "*", session: Session = Depends(get_session)):
    """立刻往该城市对应的渠道发一条自检消息，返回每个渠道的成败与原因。

    不走队列 —— 配置页要的是"点一下马上知道通不通"，而不是 15 秒后去翻台账。
    """
    routes = notify._pick_routes(session, city, "critical")
    if not routes:
        return {"ok": False, "detail": f"城市「{city}」没有匹配的渠道（也没有 * 兜底）"}
    results = []
    for route in routes:
        text = (
            "**🔵 提示｜发布室通道自检**\n\n"
            f"> 城市：{route.city}\n"
            f"> 渠道：{route.channel}\n\n"
            "看到这条说明通道是通的，后续告警会发到这里。"
        )
        if route.channel == "email":
            ok, err = notify.send_email(route, "[发布室] 通道自检", text.replace("**", ""))
        else:
            ok, err = notify.send_wecom(route, text, [])
        results.append({"city": route.city, "channel": route.channel, "ok": ok, "error": err})
    return {"ok": any(r["ok"] for r in results), "results": results}


@router.get("/dispatches")
def list_dispatches(limit: int = 50, session: Session = Depends(get_session)):
    """发送台账：看哪些告警发出去了、哪些卡住了、为什么。"""
    limit = max(1, min(limit, 300))
    rows = session.exec(
        select(AlertDispatch).order_by(AlertDispatch.created_at.desc()).limit(limit)
    ).all()
    return normalize_datetimes([
        {
            "id": r.id, "event": r.event, "severity": r.severity, "city": r.city,
            "title": r.title, "status": r.status, "channel": r.channel,
            "attempts": r.attempts, "last_error": r.last_error,
            "task_id": r.task_id, "device_id": r.device_id,
            "created_at": r.created_at, "sent_at": r.sent_at,
        }
        for r in rows
    ])
