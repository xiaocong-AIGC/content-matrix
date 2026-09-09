"""告警外发：入队（永不失败）+ 派发（带重试）+ 渠道（企微群 / 邮件）。

分工是这套东西的命门：

* 业务代码**只调 `enqueue()`**。它做去重判断、写一行 pending 就返回，**永远不发 HTTP**。
* 真正的发送在 `dispatch_pending()` 里，由后台 loop 驱动，失败退避重试。

⚠ `enqueue()` 必须开自己的 Session、并吞掉一切异常。它的调用方包括
`update_agent_status` —— 那是跑在 Agent 上报状态的 HTTP 请求线程里的。一旦
`dedup_key` 撞唯一约束抛 IntegrityError 而共用了调用方的 session，SQLAlchemy 会把整个
session 置成 rollback-only，Agent 的终态写入随之失败；而 Agent 侧对 updateStatus 异常
只写一行本地状态、不重试，于是任务永远停在 running、永远续租、永不过期，**这台机再也
领不到任务**。等于通知系统的一个唯一约束获得了停掉一台设备的能力。
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlmodel import Session, select

from app.models.entities import AlertDispatch, NotifyRoute, utcnow

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
GLOBAL_CITY = "__global__"
# DeviceAccount.city 的默认值就是「未分组」，所以"没设城市"在库里长这样。
# 它不是一个城市：不能当内容池、不能进城市路由、不该出现在消息里。
UNSET_CITIES = {"", "未分组", "未分类", GLOBAL_CITY}


def real_city(value: str | None) -> str:
    """城市值 → 真实城市名；没设城市返回空串。"""
    return "" if (value or "").strip() in UNSET_CITIES else (value or "").strip()

FALLBACK_CITY = "*"
MAX_ATTEMPTS = 5
# 退避：15s → 1m → 5m → 20m → 放弃
BACKOFF_SECONDS = [15, 60, 300, 1200]


# --------------------------------------------------------------------------
# 入队
# --------------------------------------------------------------------------
def enqueue(
    *,
    event: str,
    dedup_key: str,
    title: str,
    severity: str = "warning",
    city: str = "",
    device_id: int | None = None,
    account_id: int | None = None,
    task_id: int | None = None,
    screenshot_id: int | None = None,
    payload: dict | None = None,
) -> bool:
    """写一行待发告警。**任何情况下都不抛异常、不影响调用方的事务。**

    返回 True 表示新入队，False 表示被去重掉或出错（调用方不需要关心）。
    """
    try:
        from app.db.session import engine

        with Session(engine) as own:  # 自己的 session，和调用方完全隔离
            exists = own.exec(
                select(AlertDispatch).where(AlertDispatch.dedup_key == dedup_key)
            ).first()
            if exists is not None:
                return False
            own.add(
                AlertDispatch(
                    dedup_key=dedup_key[:200],
                    event=event[:48],
                    severity=severity,
                    city=(city or "")[:40],
                    device_id=device_id,
                    account_id=account_id,
                    task_id=task_id,
                    screenshot_id=screenshot_id,
                    title=(title or "")[:200],
                    payload_json=json.dumps(payload or {}, ensure_ascii=False),
                )
            )
            own.commit()
            return True
    except Exception as exc:  # noqa: BLE001 — 通知永远不能影响业务
        # 但**必须留痕**。这里静默吞掉过一次代价极高的失败：调用点写在调用方
        # commit 之前，SQLite 的写锁在本线程手上，内层 INSERT 等的正是自己，
        # 超时抛 "database is locked" 后被吞掉 —— 转人工告警因此 114 次一条没发出去，
        # 而线上完全看不出任何异常。吞异常可以，但不能连一行日志都不留。
        try:
            log.warning("告警入队失败，这条通知丢了：%s（%s）", title, exc)
        except Exception:  # noqa: BLE001 — 日志本身也可能失败
            pass
        return False


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------
def _pick_routes(session: Session, city: str, severity: str) -> list[NotifyRoute]:
    """按城市挑收件渠道：先精确城市，取不到用 `*` 兜底；系统级事件用 `__global__`。"""
    wanted = SEVERITY_ORDER.get(severity, 1)
    candidates = session.exec(
        select(NotifyRoute).where(NotifyRoute.enabled == True)  # noqa: E712
    ).all()
    exact = [r for r in candidates if r.city == city and city]
    chosen = exact or [r for r in candidates if r.city == FALLBACK_CITY]
    if city == GLOBAL_CITY:
        chosen = [r for r in candidates if r.city == GLOBAL_CITY] or chosen
    return [
        r
        for r in chosen
        if SEVERITY_ORDER.get(r.min_severity, 1) <= wanted and r.target.strip()
    ]


def _in_quiet_hours(route: NotifyRoute, moment: datetime) -> bool:
    """quiet_hours 形如 `23:00-08:00`（按中国时区）。空串或格式不对一律不静默。"""
    spec = (route.quiet_hours or "").strip()
    if "-" not in spec:
        return False
    try:
        start_s, end_s = spec.split("-", 1)
        cn = moment.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        minutes = cn.hour * 60 + cn.minute
        sh, sm = (int(x) for x in start_s.strip().split(":"))
        eh, em = (int(x) for x in end_s.strip().split(":"))
        start, end = sh * 60 + sm, eh * 60 + em
    except (ValueError, TypeError):
        return False
    return start <= minutes < end if start <= end else (minutes >= start or minutes < end)


# --------------------------------------------------------------------------
# 渠道
# --------------------------------------------------------------------------
def _post_json(url: str, body: dict, timeout: int = 10) -> tuple[bool, str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        parsed = json.loads(raw or "{}")
        if parsed.get("errcode") in (0, None):
            return True, ""
        return False, f"errcode={parsed.get('errcode')} {parsed.get('errmsg', '')}"[:300]
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return False, str(exc)[:300]


def send_wecom(route: NotifyRoute, markdown: str, mentions: list[str]) -> tuple[bool, str]:
    """企微群机器人。markdown 消息里 @人 要另发一条 text（markdown 不支持 @）。"""
    ok, err = _post_json(route.target, {"msgtype": "markdown", "markdown": {"content": markdown}})
    if ok and mentions:
        _post_json(
            route.target,
            {"msgtype": "text", "text": {"content": "↑ 需要处理", "mentioned_mobile_list": mentions}},
        )
    return ok, err


def send_email(route: NotifyRoute, subject: str, body: str) -> tuple[bool, str]:
    """SMTP。配置走环境变量，避免把口令写进库。"""
    from app.core.config import get_settings

    cfg = get_settings()
    host = getattr(cfg, "smtp_host", "") or ""
    if not host:
        return False, "未配置 SMTP（smtp_host 为空）"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = getattr(cfg, "smtp_from", "") or getattr(cfg, "smtp_user", "")
    message["To"] = route.target
    message.set_content(body)
    try:
        port = int(getattr(cfg, "smtp_port", 465) or 465)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as s:
                s.login(getattr(cfg, "smtp_user", ""), getattr(cfg, "smtp_password", ""))
                s.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(getattr(cfg, "smtp_user", ""), getattr(cfg, "smtp_password", ""))
                s.send_message(message)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — smtplib 异常种类太多
        return False, str(exc)[:300]


# --------------------------------------------------------------------------
# 文案
# --------------------------------------------------------------------------
# 严重度只决定要不要 @ 值班，不再往消息里加「🔴 严重」这种前缀 ——
# 每条通知的标题自己会说清楚发生了什么，前缀只是又占一行宽度。
_CONSOLE = "http://192.168.1.100:8080"


def render(row: AlertDispatch) -> tuple[str, str]:
    """返回 (企微 markdown, 邮件纯文本)。

    正文来自 payload 里的 `lines`（已经是写好的句子）。入队方负责把话说明白，
    这里只负责排版 —— 所以任何时候都不会有字段名漏进消息里。
    """
    payload = {}
    try:
        payload = json.loads(row.payload_json or "{}")
    except ValueError:
        pass
    lines = [f"**{row.title}**"]
    for sentence in payload.get("lines") or []:
        if sentence:
            lines.append(f"> {sentence}")
    plain = "\n".join(x.replace("**", "").replace("> ", "  ") for x in lines)
    return "\n".join(lines), plain


# --------------------------------------------------------------------------
# 派发
# --------------------------------------------------------------------------
def dispatch_pending(session: Session, limit: int = 20) -> int:
    """把待发的告警发出去。由后台 loop 每 15 秒调一次。返回本轮成功发送数。"""
    now = utcnow()
    rows = session.exec(
        select(AlertDispatch)
        .where(AlertDispatch.status == "pending")
        .where(
            (AlertDispatch.next_retry_at.is_(None))
            | (AlertDispatch.next_retry_at <= now)
        )
        .order_by(AlertDispatch.created_at)
        .limit(limit)
    ).all()
    sent = 0
    for row in rows:
        routes = _pick_routes(session, row.city, row.severity)
        routes = [r for r in routes if not _in_quiet_hours(r, now)]
        if not routes:
            row.status = "no_route"
            row.last_error = "没有匹配的收件渠道（或都在静默时段）"
            session.add(row)
            session.commit()
            continue
        markdown, plain = render(row)
        ok_any = False
        last_error = ""
        for route in routes:
            mentions = [
                m.strip() for m in (route.oncall_mobiles or "").split(",") if m.strip()
            ]
            if route.channel == "email":
                ok, err = send_email(route, f"[发布室] {row.title}", plain)
            else:
                ok, err = send_wecom(
                    route, markdown, mentions if row.severity == "critical" else []
                )
            if ok:
                ok_any = True
                route.consecutive_failures = 0
                row.route_id = route.id
                row.channel = route.channel
            else:
                last_error = err
                route.consecutive_failures += 1
                route.last_error = err
            route.updated_at = now
            session.add(route)
        row.attempts += 1
        if ok_any:
            row.status = "sent"
            row.sent_at = now
            sent += 1
        elif row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.last_error = last_error
        else:
            delay = BACKOFF_SECONDS[min(row.attempts - 1, len(BACKOFF_SECONDS) - 1)]
            row.next_retry_at = now + timedelta(seconds=delay)
            row.last_error = last_error
        session.add(row)
        session.commit()
    return sent
