"""内容自动补货的设置和试算。

后端那套（算缺口 → 生成 → 机器过闸 → 进池）在 `services/content_supply.py` 里
已经跑得通，但**一直没有入口**：开关、水位天数、单轮上限、每城地名词表都只能
改库或者 curl。等于这个功能对运营不存在。

这里只做三件事：读设置+缺口、写设置、手动跑一轮。
生成本身是要花钱的，所以「跑一轮」和「看缺口」必须分开 —— 看缺口不产生任何生成。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.session import get_session
from app.services import content_supply, settings_store, supply_trigger

router = APIRouter(prefix="/supply", tags=["supply"])


def _tokens_text(session: Session) -> str:
    return settings_store.get(session, content_supply.CITY_TOKENS_KEY, "")


def _state(session: Session) -> dict:
    gaps = content_supply.city_gaps(session)
    return {
        "enabled": content_supply.enabled(session),
        "enabled_cities": sorted(content_supply.enabled_cities(session)),
        "min_days": content_supply.min_days(session),
        "batch_cap": content_supply.batch_cap(session),
        "quality_floor": content_supply.quality_floor(session),
        # 原始串给"高级"编辑用，解析结果给表格用 —— 两个都给，前端不用自己解析
        "city_tokens_raw": _tokens_text(session),
        "city_tokens": content_supply.city_tokens(session),
        "gaps": gaps,
        "short_total": sum(g["short"] for g in gaps),
        "last_run": settings_store.get(session, "supply:last_run", "") or None,
    }


@router.get("")
def get_supply(session: Session = Depends(get_session)):
    return _state(session)


class SupplyIn(BaseModel):
    """⚠ `enabled` 保留只为兼容老调用。真正说了算的是 `enabled_cities` ——
    自动生成是**按城市开**的，各城市的库存和投放节奏不一样。"""

    enabled: bool = False
    enabled_cities: list[str] = Field(default_factory=list)
    min_days: int = Field(ge=1, le=30)
    batch_cap: int = Field(ge=1, le=100)
    quality_floor: int = Field(ge=0, le=100000)
    # 形如 `上海:外滩|静安;北京:三里屯|国贸`。前端用表格编辑，提交时拼成这个串。
    city_tokens_raw: str = ""


@router.put("")
def set_supply(payload: SupplyIn, session: Session = Depends(get_session)):
    cities = [c.strip() for c in payload.enabled_cities if c.strip()]
    settings_store.put(session, content_supply.CITIES_KEY, ",".join(cities))
    # 老的总开关跟着城市走：有城市开着就是开着。别留两个互相矛盾的真相。
    settings_store.put(session, content_supply.ENABLED_KEY, "1" if cities else "0")
    settings_store.put(session, content_supply.DAYS_KEY, str(payload.min_days))
    settings_store.put(session, content_supply.BATCH_CAP_KEY, str(payload.batch_cap))
    settings_store.put(
        session, content_supply.QUALITY_FLOOR_KEY, str(payload.quality_floor)
    )
    settings_store.put(
        session, content_supply.CITY_TOKENS_KEY, payload.city_tokens_raw.strip()
    )
    session.commit()
    if cities:
        # 打开一个城市之后**立刻**开始补，不等下一次轮询。
        # 运营是盯着屏幕点的这一下：中间只要有一段什么都不发生的空档，
        # 在他看来就是"坏了"，然后去点「现在生成一批」，反而多花一次钱。
        # 这里只叫醒后台那一轮（force=不受冷却约束），不在请求里跑生成 ——
        # 生成要调模型、要几十秒，挂在保存按钮上会让人以为界面卡死。
        supply_trigger.poke("运营保存了自动生成设置：" + "、".join(cities), force=True)
    return _state(session)


class CityTokensIn(BaseModel):
    """只改地名词表，不碰生成参数。"""

    city_tokens_raw: str = ""


@router.put("/city-tokens")
def set_city_tokens(payload: CityTokensIn, session: Session = Depends(get_session)):
    """单独保存各城市的地名。

    ⚠ 为什么不复用 `PUT /supply`：那个接口收的是**整个设置对象**，
    只想给新城市补一行地名也会把四个生成参数一并写回，反过来只想调「一次最多」
    也会把整张词表重写一遍。而这张词表是「正文里出现别的城市地名就不入库」
    这道硬闸的全部依据 —— 被另一次保存覆盖掉，闸就静默失效了，
    而失效的表现是一篇写着上海商圈的文案挂在北京号上，读者看得到。
    """
    raw = (payload.city_tokens_raw or "").strip()
    settings_store.put(session, content_supply.CITY_TOKENS_KEY, raw)
    return _state(session)


@router.post("/run")
def run_supply(session: Session = Depends(get_session)):
    """立刻补一轮。**会真的调模型生成、真的花钱**，所以只在运营明确点击时发生。

    开关关着的时候后端会直接跳过 —— 这里保持那个行为，不给"没开也能偷偷跑"的口子。
    """
    result = content_supply.run_once(session)
    # 播报必须在 run_once 内部 commit 之后（同 tasks.py 那条：commit 前 enqueue
    # 会和 SQLite 的写锁自锁，异常被吞掉，通知就永远不响）
    content_supply.notify_result(session, result)
    return {"result": result, **_state(session)}
