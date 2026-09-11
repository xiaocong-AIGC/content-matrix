"""内容自动补货：按城市算缺口 → 生成 → 机器过闸 → 进池。

**为什么必须做这个**：这套系统现在不是自持的。本周写了 81 篇、发了 93 篇，库存从 66
掉到 54 —— 补货全靠人记得去点「生成」按钮，而 AI 本身一周只花两毛钱。瓶颈从来不是
成本，是那一下点击。

两个反直觉但已经用数据定过的口径（见 docs/自动发布方案.md 第 4 节）：

**① 城市决定"写哪里"，不决定"学谁"。** 曾经想过"每个城市只学本城爆款"，被这张表
证伪：北京自己的 TOP10 互动是 41~96，只有上海（140~459）/深圳（156~501）的**五分之一**，
而且最近一次回采是 33 天前。按"本城优先"北京会永远凑得满、永远不降级，于是每天学
自己一个月前最差的那批，产物再回流样本池 —— 自我强化的低水平循环。
所以样本按**质量分位从全矩阵借**，城市只决定内容写谁。

**② 地名泄漏必须确定性拦截，不能靠 prompt。** 生成温度是 1.0、样本里全是外地爆款、
自动过审时无人过目 —— LLM 对负向指令的遵守率在这个量级下一定不是 100%。而单次污染
的后果是一个北京号发了一篇写上海商圈的探店文，**这是对外可见的运营事故**。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from app.core.enums import ContentStatus
from app.models.entities import ContentDraft, ContentItem, DeviceAccount, utcnow
from app.services import settings_store
from app.services.metrics import top_posts_for_remix

# 库存低于几天就补货。不是每天无脑生成 —— 那样内容会堆到发不完，还白花钱。
DAYS_KEY = "supply:min_days"
DEFAULT_MIN_DAYS = 3
# 一次最多生成几条，防止某天缺口算大了一次性烧掉一大笔
BATCH_CAP_KEY = "supply:batch_cap"
DEFAULT_BATCH_CAP = 10
ENABLED_KEY = "supply:enabled"
# 开启了哪些城市，逗号分隔。**按城市开**，不是一个总开关一开全生成 ——
# 各城市的库存、内容质量、投放节奏都不一样，上海缺 14 篇不代表北京也该开着写。
CITIES_KEY = "supply:cities"
# 每城的地名词表，用来做地名泄漏的确定性拦截。逗号分隔。
CITY_TOKENS_KEY = "supply:city_tokens"

_LAST_RUN_KEY = "supply:last_run"

GENERIC = ("通用", "全国", "")


def enabled_cities(session: Session) -> set[str]:
    """开着自动生成的城市。空集合 = 一个都没开，等于整体关闭。"""
    raw = settings_store.get(session, CITIES_KEY, "")
    return {c.strip() for c in raw.split(",") if c.strip()}


def enabled(session: Session) -> bool:
    """还有没有城市开着。

    ⚠ 老的 `supply:enabled` 总开关保留只为兼容：它是 "1" 时视作"所有有缺口的
    城市都开着"。新的写法是 `supply:cities`，一城一开关。
    """
    if enabled_cities(session):
        return True
    return settings_store.get(session, ENABLED_KEY, "") == "1"


def min_days(session: Session) -> int:
    try:
        return max(int(settings_store.get(session, DAYS_KEY, "")), 1)
    except (TypeError, ValueError):
        return DEFAULT_MIN_DAYS


def batch_cap(session: Session) -> int:
    try:
        return max(int(settings_store.get(session, BATCH_CAP_KEY, "")), 1)
    except (TypeError, ValueError):
        return DEFAULT_BATCH_CAP


# ---------------------------------------------------------------------------
# 缺口
# ---------------------------------------------------------------------------
def city_gaps(session: Session, include_ok: bool = False) -> list[dict]:
    """每个城市还差多少篇才够 min_days 天。

    分母只算**还在这套系统里跑的号**（最近 48 小时发过的）—— 和通知那边同一个口径，
    否则会为 8 个走别的方式发布的号凭空生成内容。

    `include_ok=True` 时把**够用的城市也带上**（`short` 为 0 或负数）。

    ⚠ 为什么需要这个开关：默认只返回有缺口的城市，于是界面上唯一真正在跑的
    那个城市（深圳，全矩阵仅有的 5 个自动发布账号都在那）**整行都是破折号** ——
    库存 45 篇、正好压在 45 篇的线上，而运营在页面上一个数字也看不到，
    只看到「够用」。等它掉下去变成有缺口才第一次显示数字，那时已经晚了。
    生成用的还是只看有缺口的那份（`run_once` 用默认值），显示用这一份。
    """
    from app.services.notify_sweep import _accounts_by_city, NO_CITY
    from app.services.city_policy import policies_by_city, target_for

    pmap = policies_by_city(session)
    days = min_days(session)
    buckets = _accounts_by_city(session)

    rows = session.exec(
        select(ContentItem.city, func.count())
        .where(ContentItem.status == ContentStatus.PENDING)
        .group_by(ContentItem.city)
    ).all()
    stock = {(c or "通用"): n for c, n in rows}
    shared = sum(n for c, n in rows if (c or "") in GENERIC)

    gaps = []
    for city, members in buckets.items():
        if city == NO_CITY:
            continue          # 没设城市的号只能吃通用池，不为它单独生成
        # 按城市自己的篇数算需求 —— 北京 1 篇、深圳 3 篇，备货量就该不一样
        per_day = sum(target_for(session, a, pmap) for a in members)
        need = per_day * days
        have = stock.get(city, 0)
        if have < need or include_ok:
            gaps.append({
                "city": city,
                "accounts": len(members),
                "have": have,
                "need": need,
                "short": need - have,
                # 「够发几天」比「还差几篇」更能让人当场判断要不要动手 ——
                # 45 篇听不出松紧，「够发 3 天」一听就知道。
                "days_left": round(have / per_day, 1) if per_day else None,
                "per_day": per_day,
            })
    # 通用池按"所有城市共用"算一份保底
    total_heads = sum(len(v) for k, v in buckets.items() if k != NO_CITY)
    generic_need = max(total_heads, 1) * days // 2      # 通用只做一半的保底量
    if shared < generic_need or include_ok:
        generic_per_day = generic_need / days if days else 0
        gaps.append({
            "city": "通用", "accounts": total_heads,
            "have": shared, "need": generic_need, "short": generic_need - shared,
            "days_left": round(shared / generic_per_day, 1) if generic_per_day else None,
            "per_day": round(generic_per_day, 1),
        })
    gaps.sort(key=lambda g: -g["short"])
    return gaps


# ---------------------------------------------------------------------------
# 样本：城市决定写哪里，质量决定学谁
# ---------------------------------------------------------------------------
QUALITY_FLOOR_KEY = "supply:quality_floor"
DEFAULT_QUALITY_FLOOR = 120


def quality_floor(session: Session) -> int:
    try:
        return max(int(settings_store.get(session, QUALITY_FLOOR_KEY, "")), 0)
    except (TypeError, ValueError):
        return DEFAULT_QUALITY_FLOOR


def exemplars_for(session: Session, platform: str, city: str, limit: int = 5) -> list[dict]:
    """挑样本。**本城只在质量过线时才占槽，过不了线就让全矩阵顶上。**

    这不是降级，是设计行为：北京本城 TOP 只有 41~96、低于质量线，与其让它学自己
    最差的那批，不如学全矩阵最好的、然后写北京的内容。
    """
    pool = top_posts_for_remix(session, platform, limit=60)
    if not pool:
        return []
    floor = quality_floor(session)
    own_ids = {
        c.id for c in session.exec(
            select(ContentItem).where(ContentItem.city == city)
        ).all()
    } if city and city not in GENERIC else set()

    own = [p for p in pool if p.get("content_id") in own_ids and p["engagement"] >= floor]
    rest = [p for p in pool if p not in own]
    rest.sort(key=lambda p: -p["engagement"])
    picked = own[:2] + rest[: limit - min(len(own), 2)]
    return picked[:limit]


# ---------------------------------------------------------------------------
# 机器闸：地名泄漏 / 字数 / 城市白名单 / 查重
# ---------------------------------------------------------------------------
def city_tokens(session: Session) -> dict[str, list[str]]:
    """每城的地名词表。形如 `上海:外滩|静安|陆家嘴;北京:三里屯|国贸`。"""
    raw = settings_store.get(session, CITY_TOKENS_KEY, "")
    table: dict[str, list[str]] = {}
    for chunk in raw.split(";"):
        if ":" not in chunk:
            continue
        city, _, words = chunk.partition(":")
        items = [w.strip() for w in words.split("|") if w.strip()]
        if city.strip() and items:
            table[city.strip()] = items
    return table


def check_draft(
    session: Session, *, city: str, cover: str, body: str
) -> tuple[bool, str]:
    """自动过审的机器闸。返回 (能不能自动进池, 拦下来的理由)。

    只做**确定性**判断 —— 能被规则说清的才拦，剩下的交给人工。
    """
    text = f"{cover}\n{body}"
    if len(body.strip()) < 40:
        return False, "正文太短"
    if len(body) > 2000:
        return False, "正文太长"

    # 地名泄漏：正文里出现了**别的城市**的地名。这条是硬闸，理由见模块开头。
    table = city_tokens(session)
    for other, words in table.items():
        if other == city or city in GENERIC:
            continue
        for word in words:
            if word and word in text:
                return False, f"正文里出现了{other}的「{word}」，和目标城市对不上"

    # 时效性表述：具体日期/星期会让内容过几天就过期
    if re.search(r"(今天|明天|后天|本周[一二三四五六日天]|周[一二三四五六日天]|"
                 r"\d{1,2}月\d{1,2}[日号])", text):
        return False, "写了具体日期或星期，过几天就不能用了"

    # 查重：和库里已有内容的封面撞了
    same = session.exec(
        select(ContentItem).where(ContentItem.cover_title == cover.strip())
    ).first()
    if cover.strip() and same is not None:
        return False, "封面和库里已有的一条重了"
    return True, ""


# ---------------------------------------------------------------------------
# 补货
# ---------------------------------------------------------------------------
def run_once(session: Session, now: datetime | None = None) -> dict:
    """算缺口 → 生成 → 过闸 → 进池。返回这一轮干了什么，给通知用。"""
    now = now or datetime.now(timezone.utc)
    if not enabled(session):
        return {"skipped": "没有开启任何城市"}
    gaps = city_gaps(session)
    if not gaps:
        settings_store.put(session, _LAST_RUN_KEY, now.isoformat())
        return {"gaps": [], "made": 0, "accepted": 0, "rejected": []}

    from app.services import content_engine

    cap = batch_cap(session)
    made = accepted = 0
    rejected: list[str] = []
    done_cities: list[str] = []
    # 只给**开着的城市**生成。没配 supply:cities 时退回老的总开关语义（全开）。
    allowed = enabled_cities(session)
    for gap in gaps:
        if allowed and gap["city"] not in allowed:
            continue
        if made >= cap:
            break
        want = min(gap["short"], cap - made)
        try:
            drafts = content_engine.generate_drafts(
                session, count=want, platform="douyin",
                theme=f"面向{gap['city']}的本地内容",
            )
        except Exception as exc:  # noqa: BLE001 — 一个城市生成失败不该拖垮其它城市
            rejected.append(f"{gap['city']}生成失败：{str(exc)[:60]}")
            continue
        made += len(drafts)
        done_cities.append(gap["city"])
        for draft in drafts:
            ok, why = check_draft(
                session, city=gap["city"],
                cover=draft.cover_title or "", body=draft.body or "",
            )
            if not ok:
                draft.status = "rejected"
                # 现在有专门的字段了。dup_of 复原成它本来的意思（查重来源），
                # 否则「模型老犯什么错」和「跟哪条撞了」永远混在一列里数不清。
                draft.reject_reason = why[:200]
                session.add(draft)
                rejected.append(f"{gap['city']}：{why}")
                continue
            item = ContentItem(
                cover_title=draft.cover_title or "",
                title=draft.title or "",
                body=draft.body or "",
                topics_json=draft.topics_json or "[]",
                city=gap["city"],
                platform="douyin",
                status=ContentStatus.PENDING,
                # 溯源必须走这条路 —— 自动生成是主路径（每天一半以上的内容
                # 从这里进池，而且不经人手），漏了它等于只给人工那条路记账。
                draft_id=draft.id,
                prompt_version_id=draft.prompt_version_id,
            )
            session.add(item)
            session.flush()
            draft.status = "accepted"
            draft.accepted_content_id = item.id
            session.add(draft)
            accepted += 1
        session.commit()

    settings_store.put(session, _LAST_RUN_KEY, now.isoformat())
    return {
        "gaps": [f"{g['city']} 差 {g['short']} 篇" for g in gaps],
        "cities": done_cities, "made": made,
        "accepted": accepted, "rejected": rejected,
    }


def notify_result(session: Session, result: dict) -> None:
    """把这一轮补货的结果播到群里。**必须在 commit 之后调**（见 tasks.py 那段注释）。"""
    if result.get("skipped") or not result.get("made"):
        return
    from app.services import notify

    lines = [f"生成 {result['made']} 条，入库 {result['accepted']} 条"]
    if result.get("cities"):
        lines.append("补的是：" + "、".join(result["cities"]))
    dropped = result.get("rejected") or []
    if dropped:
        lines.append(f"挡下 {len(dropped)} 条：" + "；".join(dropped[:3]))
    notify.enqueue(
        event="content_supplied",
        dedup_key=f"supply:{utcnow():%Y%m%d%H}",
        title=f"✅ 自动补了 {result['accepted']} 条内容",
        severity="info",
        payload={"lines": lines},
    )
