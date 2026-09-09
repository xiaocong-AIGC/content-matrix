"""群推送消息库 + 自动推送的设置。

消息库是**可反复用**的（和作品池不一样，作品发一次就没了），所以这里是标准的
增删改查，不涉及"消费"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from app.db.session import get_session
from app.models.entities import BroadcastMessage, DeviceAccount, ImageAsset, utcnow
from app.services import auto_broadcast, settings_store
from app.services.schedule_windows import describe, parse_windows

router = APIRouter(prefix="/broadcast-library", tags=["broadcast-library"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    image_id: int | None = None
    mention_all: bool = False
    city: str = "通用"
    enabled: bool = True


def _serialize(row: BroadcastMessage, session: Session | None = None) -> dict:
    # 前端要的是**能直接放进 <img> 的地址**。库里存的 image_path 是服务器上的
    # 绝对文件路径，拼到 origin 后面只会 404 —— 缩略图全是碎图标。
    # 图片实际由 /api/v1/images/{id}/file 提供，所以这里按路径反查一次 id。
    image_id = None
    if row.image_path and session is not None:
        asset = session.exec(
            select(ImageAsset).where(ImageAsset.path == row.image_path)
        ).first()
        image_id = asset.id if asset else None
    return {
        "id": row.id,
        "text": row.text,
        "image_path": row.image_path,
        "image_id": image_id,
        "image_url": f"/api/v1/images/{image_id}/file" if image_id else None,
        "mention_all": row.mention_all,
        "city": row.city,
        "enabled": row.enabled,
        "sent_count": row.sent_count,
        "last_sent_at": row.last_sent_at,
        "created_at": row.created_at,
    }


@router.get("")
def list_messages(session: Session = Depends(get_session)):
    rows = session.exec(
        select(BroadcastMessage).order_by(BroadcastMessage.created_at.desc())
    ).all()
    return [_serialize(r, session) for r in rows]


@router.post("", status_code=201)
def create_message(payload: MessageIn, session: Session = Depends(get_session)):
    image_path = None
    if payload.image_id is not None:
        asset = session.get(ImageAsset, payload.image_id)
        if not asset:
            raise HTTPException(status_code=404, detail="图片不存在")
        image_path = asset.path
    row = BroadcastMessage(
        text=payload.text.strip(),
        image_path=image_path,
        mention_all=payload.mention_all,
        city=(payload.city or "通用").strip() or "通用",
        enabled=payload.enabled,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize(row, session)


# ---------------------------------------------------------------------------
# ⚠ 路径顺序有讲究：`/schedule` 这一组必须声明在 `/{message_id}` **前面**。
# FastAPI 按声明顺序匹配，反过来的话 `PUT /broadcast-library/schedule` 会被
# 当成「更新 id 为 'schedule' 的那条消息」→ 422，而且报的是
# 「message_id 不是整数」这种和实际动作完全对不上的错。
# 这个坑让「保存设置」从写出来到上线一次都没成功过。
#
# 自动推送的设置：
# ---------------------------------------------------------------------------
class ScheduleIn(BaseModel):
    daily_count: int = Field(ge=0, le=20)
    windows: str
    mode: str = "same"


@router.get("/schedule")
def get_schedule(session: Session = Depends(get_session)):
    slots = auto_broadcast.windows(session)
    enabled = [
        a.nickname for a in session.exec(
            select(DeviceAccount)
            .where(DeviceAccount.platform == "douyin")
            .where(DeviceAccount.auto_broadcast == True)  # noqa: E712
        ).all() if a.nickname
    ]
    usable = session.exec(
        select(func.count())
        .select_from(BroadcastMessage)
        .where(BroadcastMessage.enabled == True)  # noqa: E712
    ).one()
    return {
        "usable_messages": usable,
        "daily_count": auto_broadcast.daily_count(session),
        # ⚠ 这两个数**不是一回事**，界面上千万别混：
        # queued_now = 已到点、还没被手机领走的（任务只在时刻到点后才创建，
        #   所以正常是 0~3 条）。关闭按钮上要显示它 —— 那是点下去会取消掉的量。
        # upcoming_today = 今天还要推几条。运营问"还剩多少"问的是这个。
        "queued_now": auto_broadcast.pending_today(session),
        "upcoming_today": auto_broadcast.upcoming_today(session),
        "windows": settings_store.get(
            session, auto_broadcast.WINDOWS_KEY, auto_broadcast.DEFAULT_WINDOWS
        ),
        "windows_readable": describe(slots),
        "mode": auto_broadcast.mode(session),
        "accounts_on": enabled,
    }


@router.put("/schedule")
def set_schedule(payload: ScheduleIn, session: Session = Depends(get_session)):
    # ⚠ 无条件校验。以前只在次数 > 0 时校验，于是次数为 0 时可以存进一个乱串，
    # 哪天把次数调回 2，就按空时间段静默地跑 —— 一条都不推，界面上毫无征兆。
    parsed = parse_windows(payload.windows)
    if payload.windows.strip() and not parsed:
        raise HTTPException(
            status_code=400,
            detail="时间段没填对。写成「10-11,14-15」或「10:00-11:00,14:00-15:00」，"
                   "不支持跨零点",
        )
    if payload.daily_count > 0 and not parsed:
        raise HTTPException(status_code=400, detail="要自动推送，得先填时间段")
    if payload.mode not in ("same", "rotate"):
        raise HTTPException(status_code=400, detail="mode 只能是 same 或 rotate")
    settings_store.put(session, auto_broadcast.COUNT_KEY, str(payload.daily_count))
    settings_store.put(session, auto_broadcast.WINDOWS_KEY, payload.windows.strip())
    settings_store.put(session, auto_broadcast.MODE_KEY, payload.mode)
    return get_schedule(session)


@router.post("/schedule/stop")
def stop_schedule(session: Session = Depends(get_session)):
    """立刻停止自动推送。**不只是关开关** —— 同时取消今天还排着没执行的那些。

    只把次数置 0 的话，今天已经进队列的群消息照发：运营点了停止、群里还在冒消息。

    `in_flight` 是已经被手机领走、正在发的那几条 —— **停不下来**。
    界面写「全部停止」而 20 秒后群里又冒一条，是这个功能最伤信任的失败。
    """
    flying = auto_broadcast.in_flight(session)
    cancelled = auto_broadcast.stop_now(session)
    # 企微群是运营的主界面：一个人在控制台点了停止，其他人只会发现群里今天特别安静
    try:
        from app.services import notify

        notify.enqueue(
            event="broadcast_stopped",
            dedup_key=f"bcstop:{utcnow():%Y%m%d%H%M}",
            title="自动推送已停止",
            severity="info",
            payload={"lines": [
                f"取消了今天还没推的 {cancelled} 条",
                "需要恢复的话，去控制台把每天次数设回去",
            ]},
        )
    except Exception:  # noqa: BLE001 — 通知发不出去不该让停止本身失败
        pass
    return {"cancelled": cancelled, "in_flight": flying, **get_schedule(session)}


class PreviewIn(BaseModel):
    """试算用的一套还没保存的设置。不传就是看当前真实情况。"""

    daily_count: int = Field(ge=0, le=20)
    windows: str
    mode: str = "same"


@router.get("/schedule/preview")
def preview_schedule(session: Session = Depends(get_session)):
    """今天每个号几点推、推的是**哪一条**、现在什么状态。

    只回时刻的话运营什么也判断不了 —— 他要确认的是"今天群里会冒出哪句话"。
    已排的读真实任务，待排的按和排程同一套种子推算（时刻和挑法都可复现）。
    """
    return auto_broadcast.preview_envelope(session)


@router.post("/schedule/preview")
def dry_run_schedule(
    payload: PreviewIn | None = None, session: Session = Depends(get_session)
):
    """按一套**还没保存**的设置试算今天会怎么推。不传 body 等同于 GET。

    试算只换设置、不换算法（走同一个 `preview_today`）—— 在路由层另写一遍取值
    逻辑的话，试算和实际就会各说各的，而这正是这一页存在的意义所在。
    """
    if payload is None:
        return auto_broadcast.preview_envelope(session)
    if payload.windows.strip() and not parse_windows(payload.windows):
        raise HTTPException(status_code=400, detail="时间段没填对，试算不了")
    return auto_broadcast.preview_envelope(
        session,
        override={
            "daily_count": payload.daily_count,
            "windows": payload.windows,
            "mode": payload.mode,
        },
    )


@router.put("/{message_id}")
def update_message(
    message_id: int, payload: MessageIn, session: Session = Depends(get_session)
):
    row = session.get(BroadcastMessage, message_id)
    if not row:
        raise HTTPException(status_code=404, detail="这条消息不存在")
    # image_id 为空 = 这条不带图。和新增保持同一个语义 ——
    # 原来"空就保留原图"的写法意味着图片一旦设上就再也撤不掉。
    if payload.image_id is None:
        row.image_path = None
    else:
        asset = session.get(ImageAsset, payload.image_id)
        if not asset:
            raise HTTPException(status_code=404, detail="图片不存在")
        row.image_path = asset.path
    row.text = payload.text.strip()
    row.mention_all = payload.mention_all
    row.city = (payload.city or "通用").strip() or "通用"
    row.enabled = payload.enabled
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize(row, session)


@router.delete("/{message_id}", status_code=204)
def delete_message(message_id: int, session: Session = Depends(get_session)):
    row = session.get(BroadcastMessage, message_id)
    if row:
        session.delete(row)
        session.commit()
