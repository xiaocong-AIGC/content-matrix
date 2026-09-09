import json

from sqlmodel import Session, func, select

from app.core.enums import TaskStatus
from app.models.entities import ContentItem, PostMetric, PublishTask, utcnow


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
    rows = []
    for cid, m in latest.items():
        content = contents.get(cid)
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
            }
        )
    rows.sort(key=lambda r: r["engagement"], reverse=True)
    return rows[:limit]


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
