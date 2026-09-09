"""自动发布的时间段。

用户对群推送和发布提的是同一条要求：「每天自动推送次数可以设置，然后时间可以在
规定的时间内随机，比如我选时间为：10-11，14-15……**发布也是**」。

群推送那半边已经有设置入口了，发布这半边一直只有 `tasks.py` 在读
`publish:windows`，**没有任何地方能写** —— 等于这个能力对运营不存在。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db.session import get_session
from app.services import settings_store
from app.services.schedule_windows import describe, parse_windows
from app.services.tasks import PUBLISH_WINDOWS_KEY

router = APIRouter(prefix="/publish", tags=["publish"])


def _state(session: Session) -> dict:
    raw = settings_store.get(session, PUBLISH_WINDOWS_KEY, "")
    slots = parse_windows(raw)
    return {
        "windows": raw,
        "windows_readable": describe(slots),
        # 空 = 不限时段，手机一空就发（这也是一直以来的行为）
        "unrestricted": not slots,
    }


@router.get("/windows")
def get_windows(session: Session = Depends(get_session)):
    return _state(session)


class WindowsIn(BaseModel):
    windows: str = ""


@router.put("/windows")
def set_windows(payload: WindowsIn, session: Session = Depends(get_session)):
    """留空 = 不限时段。填了就必须能解析 —— 存进一个乱串等于静默地一条都不发。"""
    raw = (payload.windows or "").strip()
    if raw and not parse_windows(raw):
        raise HTTPException(
            status_code=400,
            detail="时间段没填对。写成「10-11,14-15」或「10:00-11:00,14:00-15:00」，"
                   "不支持跨零点",
        )
    settings_store.put(session, PUBLISH_WINDOWS_KEY, raw)
    return _state(session)
