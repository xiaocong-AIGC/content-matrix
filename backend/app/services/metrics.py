import json
from datetime import datetime, timezone

from sqlmodel import Session, func, select

from app.core.enums import TaskStatus
from app.models.entities import ContentItem, PostMetric, PublishTask, utcnow
from app.services.content_taxonomy import classify, tier


def _match_task(
    session: Session, device_id: int, platform: str, title: str
) -> PublishTask | None:
    """Find the published task this 回采ed note belongs to: most recent succeeded
    post on the same device+platform whose cover/title/body shares the note text."""
    if not title.strip():
        return None
    candidates = session.exec(
        select(PublishTask)
        .where(PublishTask.device_id == device_id)
        .where(PublishTask.platform == platform)
        .where(PublishTask.status == TaskStatus.SUCCEEDED)
        .where(PublishTask.publish_type != "group_message")
        .order_by(PublishTask.finished_at.desc())
        .limit(50)
    ).all()
    t = title.strip()
    for task in candidates:
        for field in (task.cover_title, task.publish_title, task.body, task.name):
            f = (field or "").strip()
            if f and (f[:12] in t or t[:12] in f):
                return task
    return None


def _latest_metric(
    session: Session, device_id: int, platform: str, task_id, title: str
) -> PostMetric | None:
    stmt = (
        select(PostMetric)
        .where(PostMetric.device_id == device_id)
        .where(PostMetric.platform == platform)
        .order_by(PostMetric.captured_at.desc())
    )
    stmt = stmt.where(PostMetric.task_id == task_id) if task_id else stmt.where(
        PostMetric.title == title[:200]
    )
    return session.exec(stmt).first()


def record_metrics(
    session: Session, device_id: int, platform: str, posts: list
) -> int:
    """Store a metric snapshot per reported post, linked to the matched task /
    content. A re-collected note only adds a NEW snapshot when its counts changed
    (keeps a time-series of real changes, no duplicate rows). Returns # stored."""
    n = 0
    for p in posts:
        task = _match_task(session, device_id, platform, p.title)
        prev = _latest_metric(
            session, device_id, platform, task.id if task else None, p.title
        )
        body = (getattr(p, "body", "") or "")[:3000]
        if (
            prev
            and prev.views == p.views
            and prev.likes == p.likes
            and prev.collects == p.collects
            and prev.comments == p.comments
        ):
            # Counts unchanged — no new snapshot. But if we captured a fuller body
            # than the stored one (e.g. now the full 正文 from the 评论 panel vs the
            # old truncated caption), update it in place so the remix engine gets
            # the better text without spamming the time-series.
            if body and len(body) > len(prev.body or ""):
                prev.body = body
                session.add(prev)
                n += 1
            continue
        session.add(
            PostMetric(
                task_id=task.id if task else None,
                content_id=task.content_id if task else None,
                device_id=device_id,
                platform=platform,
                title=p.title[:200],
                body=body,
                views=p.views,
                likes=p.likes,
                collects=p.collects,
                comments=p.comments,
                shares=p.shares,
                captured_at=utcnow(),
            )
        )
        n += 1
    session.commit()
    return n


def content_performance(session: Session, limit: int = 1000) -> list[dict]:
    """Latest metric snapshot per content, joined to the content, ranked by
    engagement (likes+collects+comments) — the 效果榜。

    ⚠ limit 以前写死 100，而线上已发布 900+ 篇：榜单被永久截断在互动前 100 名，
    新发的内容互动还低、永远挤不进去，操作员看到的数字几个月一动不动（"我都更新
    好多次了还是 100 篇"）。默认给到 1000，并由路由暴露出去。
    """
    # 只取每条内容"最新一次"快照：先按 content_id 求 max(captured_at) 再回连，
    # 不要把整张 PostMetric 拉进内存（回采是每天一次，行数只增不减）。
    newest = (
        select(
            PostMetric.content_id.label("cid"),
            func.max(PostMetric.captured_at).label("latest"),
        )
        .where(PostMetric.content_id.is_not(None))
        .group_by(PostMetric.content_id)
        .subquery()
    )
    snapshots = session.exec(
        select(PostMetric).join(
            newest,
            (PostMetric.content_id == newest.c.cid)
            & (PostMetric.captured_at == newest.c.latest),
        )
    ).all()
    latest: dict[int, PostMetric] = {}
    for m in snapshots:
        latest.setdefault(m.content_id, m)  # 同一时刻的并列快照取其一
    # 一次性把内容捞出来，别在循环里 session.get（900 条 = 900 次查询）。
    contents: dict[int, ContentItem] = {}
    if latest:
        contents = {
            c.id: c
            for c in session.exec(
                select(ContentItem).where(ContentItem.id.in_(list(latest)))
            ).all()
        }
    # 「是哪个号发的、哪天发的」—— 榜上只有标题和数字时，运营没法回答
    # 「这条是谁发的」「是不是刚发的所以数字还低」，只能一条条点进去看。
    # 三张表一次捞完，同样别在循环里查。
    from app.models.entities import Device, DeviceAccount

    tasks: dict[int, PublishTask] = {}
    if latest:
        tasks = {
            t.content_id: t
            for t in session.exec(
                select(PublishTask)
                .where(PublishTask.content_id.in_(list(latest)))
                .where(PublishTask.status == TaskStatus.SUCCEEDED)
                .order_by(PublishTask.finished_at)
            ).all()
        }
    device_ids = {t.device_id for t in tasks.values() if t.device_id}
    device_ids |= {m.device_id for m in latest.values() if m.device_id}
    devices = {
        d.id: d
        for d in session.exec(select(Device).where(Device.id.in_(device_ids))).all()
    } if device_ids else {}
    accounts: dict[tuple[int, str], DeviceAccount] = {}
    if device_ids:
        accounts = {
            (a.device_id, a.platform): a
            for a in session.exec(
                select(DeviceAccount).where(DeviceAccount.device_id.in_(device_ids))
            ).all()
        }
    rows = []
    for cid, m in latest.items():
        content = contents.get(cid)
        # 分类看封面。回采到的、对不上内容库的帖子只有 m.title，
        # 那串是「封面。正文开头…」拼在一起的，cover_of 会切出封面。
        cover = (content.cover_title if content else "") or m.title or ""
        task = tasks.get(cid)
        dev = devices.get(task.device_id if task else m.device_id)
        acc = accounts.get(((task.device_id if task else m.device_id), m.platform))
        rows.append(
            {
                "content_id": cid,
                "platform": m.platform,
                "title": content.title or content.cover_title or (content.body[:20] if content else "") if content else m.title,
                "cover_title": (content.cover_title if content else "") or "",
                "body": (content.body if content else "") or m.body or "",
                "topics": json.loads(content.topics_json) if content else [],
                "excerpt": (m.body or (content.body if content else "") or "")[:80],
                "views": m.views,
                "likes": m.likes,
                "collects": m.collects,
                "comments": m.comments,
                "engagement": m.likes + m.collects + m.comments,
                "captured_at": m.captured_at,
                # ── 「谁发的、哪天发的」 ──────────────────────────────
                # ⚠ 账号信息是**当下反查**的，不是发布当时的快照。刷机重登
                # 换了号，`upsert_account` 会就地覆盖 nickname（accounts.py），
                # 历史数据就会挂到新号名下。要彻底解决得在发布时存一份快照，
                # 那是另一件事；这里先把「今天这台机是谁」如实显示出来。
                # 内容风格：实测唯一稳定更好的是「二选一」（摆出两个互斥选项
                # 让人替他选），评论中位数是其余的 2.3 倍，而它只占产出的
                # 10.5%。判据看封面那一句，见 services/content_taxonomy.py。
                "content_type": classify(cover),
                "tier": tier(cover),
                "device_name": (dev.name if dev else None),
                "account_nickname": (acc.nickname if acc else None),
                "city": (acc.city if acc else (content.city if content else None)),
                "published_at": (task.finished_at if task else
                                 (content.published_at if content else None)),
                # 发出去多少天了 —— 新发的数字低是正常的，榜上必须能看出来
                "age_days": (
                    (utcnow() - _aware(task.finished_at)).days
                    if task and task.finished_at else
                    ((utcnow() - _aware(content.published_at)).days
                     if content and content.published_at else None)
                ),
            }
        )
    rows.sort(key=lambda r: r["engagement"], reverse=True)
    return rows[:limit]


def _aware(moment: datetime) -> datetime:
    """库里存的是 naive UTC，减之前先补上时区，否则 TypeError。"""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def type_summary(session: Session) -> dict:
    """「哪种写法更好」的一句话结论 —— **在全量上算，不是在榜上算**。

    ⚠ 不能拿 `content_performance` 的返回来算：那个函数是**先按互动排序再
    截断**，拿到的永远是高分子集。在偏样本上算「二选一比其余好几倍」，
    算出来的倍数是选样偏差不是效果。这个坑 2026-09-10 踩过一次
    （中位数 11000 vs 真实 4886）。

    平台分开算：抖音和小红书的评论量级差一个数量级，混在一起没有意义。
    """
    from app.services.content_taxonomy import TIER_BEST, tier

    newest = (
        select(
            PostMetric.content_id.label("cid"),
            func.max(PostMetric.captured_at).label("latest"),
        )
        .where(PostMetric.content_id.is_not(None))
        .group_by(PostMetric.content_id)
        .subquery()
    )
    snapshots = session.exec(
        select(PostMetric).join(
            newest,
            (PostMetric.content_id == newest.c.cid)
            & (PostMetric.captured_at == newest.c.latest),
        )
    ).all()
    latest: dict[int, PostMetric] = {}
    for m in snapshots:
        latest.setdefault(m.content_id, m)
    covers = {
        c.id: (c.cover_title or "")
        for c in session.exec(
            select(ContentItem).where(ContentItem.id.in_(list(latest)))
        ).all()
    } if latest else {}

    out: dict[str, dict] = {}
    for platform in ("douyin", "xhs"):
        best, rest = [], []
        for cid, m in latest.items():
            if m.platform != platform:
                continue
            cover = covers.get(cid) or m.title or ""
            (best if tier(cover) == TIER_BEST else rest).append(m.comments or 0)
        if len(best) < 5 or len(rest) < 5:
            continue     # 样本太少，一句结论比没有结论更糟

        def median(values: list[int]) -> float:
            ordered = sorted(values)
            mid = len(ordered) // 2
            return (ordered[mid] if len(ordered) % 2
                    else (ordered[mid - 1] + ordered[mid]) / 2)

        m_best, m_rest = median(best), median(rest)
        out[platform] = {
            "best_n": len(best),
            "rest_n": len(rest),
            "best_share": round(len(best) / (len(best) + len(rest)) * 100),
            # 取整：偶数条时中位数会是 97.5，而「评论 97.5 条」没有意义
            "best_median_comments": round(m_best),
            "rest_median_comments": round(m_rest),
            "ratio": round(m_best / max(m_rest, 1), 1),
        }
    return out


def top_posts_for_remix(session: Session, platform: str | None, limit: int = 10) -> list[dict]:
    """Best exemplars for the 二改 engine: the latest snapshot per note that has
    a captured body, ranked by engagement — INCLUDES unmatched 回采ed notes (often
    our best performers), not just content we published ourselves."""
    stmt = (
        select(PostMetric)
        .where(PostMetric.body != "")
        .order_by(PostMetric.captured_at.desc())
    )
    if platform and platform != "both":
        stmt = stmt.where(PostMetric.platform == platform)
    latest: dict[str, PostMetric] = {}
    for m in session.exec(stmt).all():
        key = m.title or str(m.id)
        if key not in latest:
            latest[key] = m
    rows = [
        {
            "content_id": m.content_id,
            "platform": m.platform,
            "title": m.title,
            "body": m.body,
            "views": m.views,
            "likes": m.likes,
            "collects": m.collects,
            "comments": m.comments,
            "engagement": m.likes + m.collects + m.comments,
        }
        for m in latest.values()
    ]
    # 🔴 样本必须是「我们自己发的」内容。回采会连带抓到抖音创作者中心里的官方活动/推荐位
    # 内容（如「创作阶梯计划」「抖音精选」），它们点赞上万却 views=0，按互动排序会把我们
    # 真实的爆款全挤掉 → AI 拿到的样本完全跑偏（用户实测：抖音样本 5 条全是官方推广）。所以：
    #   ① 先取能匹配到内容库的（content_id 非空 = 确定是我们发布的）；
    #   ② 不足 limit 时再用未匹配的补足，但剔除「views=0 却互动过千」的推荐位数据。
    ours = sorted(
        (r for r in rows if r.get("content_id")),
        key=lambda r: r["engagement"],
        reverse=True,
    )
    if len(ours) >= limit:
        return ours[:limit]
    # 自己的不够 limit 条（小红书匹配率低）→ 用未匹配的补，但两者合并后按互动统一排序，
    # 否则会把 0 赞的自有内容排在 2.6万浏览的爆款前面，样本质量反而更差。
    others = [
        r
        for r in rows
        if not r.get("content_id")
        and not (r["views"] == 0 and r["engagement"] > 1000)
    ]
    return sorted(ours + others, key=lambda r: r["engagement"], reverse=True)[:limit]
