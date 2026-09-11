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


# ─────────────────────────────────────────────────────────────────────
# 城市策略：开关、时段、每天几篇，按城市设
# ─────────────────────────────────────────────────────────────────────
class CityPolicyIn(BaseModel):
    """只改请求里**出现了**的字段。某个字段传 null = 这一项改回跟随全局。

    用 `model_fields_set` 区分「没传」和「传了 null」—— 两者意思完全不同：
    没传是「别动它」，传 null 是「不单独设了」。
    """

    auto_publish: bool | None = None
    windows: str | None = None
    daily_target: int | None = None


def _city_rows(session: Session) -> list[dict]:
    from sqlmodel import select as _select

    from app.models.entities import DeviceAccount
    from app.services import city_policy as cp
    from app.services.notify_sweep import daily_target

    pmap = cp.policies_by_city(session)
    global_windows = parse_windows(settings_store.get(session, PUBLISH_WINDOWS_KEY, ""))
    global_target = daily_target(session)
    accounts: dict[str, list] = {}
    for acc in session.exec(
        _select(DeviceAccount).where(DeviceAccount.platform == "douyin")
    ).all():
        if cp.is_city(acc.city) and acc.nickname:
            accounts.setdefault(acc.city, []).append(acc)

    rows = []
    for city in sorted(set(accounts) | set(pmap)):
        members = accounts.get(city, [])
        policy = pmap.get(city)
        on = [a for a in members if cp.publishes(session, a, pmap)]
        exceptions = [a for a in members if a.publish_override in cp.OVERRIDES]
        slots = (parse_windows(policy.windows)
                 if policy and policy.windows is not None else global_windows)
        rows.append({
            "city": city,
            "accounts": len(members),
            "publishing": len(on),
            "exceptions": len(exceptions),
            # 策略原值：null = 这一项跟随全局
            "auto_publish": policy.auto_publish if policy else None,
            "windows": policy.windows if policy else None,
            "daily_target": policy.daily_target if policy else None,
            # 实际生效的值，界面上显示这个
            "effective_windows": describe(slots),
            "effective_unrestricted": not slots,
            "effective_target": cp.city_target(session, city, pmap),
            # 在发的号今天一共该发几篇（逐号按 target_for 算，含单号上限）。
            # 开城市的确认框、城市卡片用这个 —— 用「号数 × 城市篇数」会和引擎
            # 实际数对不上（有号设了单独不发、有号上限低）。
            "publishing_target": sum(cp.target_for(session, a, pmap) for a in on),
            "overrides_on": sum(1 for a in members if a.publish_override == cp.OVERRIDE_ON),
            "overrides_off": sum(1 for a in members if a.publish_override == cp.OVERRIDE_OFF),
        })
    _ = global_target
    return rows


@router.get("/cities")
def list_cities(session: Session = Depends(get_session)):
    return {"cities": _city_rows(session)}


@router.put("/cities/{city}")
def put_city(city: str, payload: CityPolicyIn, session: Session = Depends(get_session)):
    from app.services import city_policy as cp

    if not cp.is_city(city):
        raise HTTPException(status_code=400, detail=f"「{city}」不是城市，不能单独设置")
    sent = payload.model_fields_set
    kwargs = {}
    if "auto_publish" in sent:
        kwargs["auto_publish"] = payload.auto_publish
    if "windows" in sent:
        raw = (payload.windows or "").strip() if payload.windows is not None else None
        # 填了就必须能解析 —— 存进一个乱串等于这个城市静默地一条都不发
        if raw and not parse_windows(raw):
            raise HTTPException(
                status_code=400,
                detail="时间段没填对。写成「10-11,14-15」或「10:00-11:00,14:00-15:00」，"
                       "不支持跨零点",
            )
        kwargs["windows"] = raw
    if "daily_target" in sent:
        value = payload.daily_target
        if value is not None and not (1 <= value <= 20):
            raise HTTPException(status_code=400, detail="每天篇数要在 1 到 20 之间")
        kwargs["daily_target"] = value
    cp.set_policy(session, city, **kwargs)
    return {"cities": _city_rows(session)}
