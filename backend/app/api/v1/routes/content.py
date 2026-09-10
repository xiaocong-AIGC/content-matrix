import csv
import io
import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlmodel import Session, func, select

from app.core.enums import ContentStatus, DeviceStatus, TaskStatus, TERMINAL_STATUSES
from app.core.serialization import normalize_datetimes
from app.db.session import get_session
from app.models.entities import (
    ContentDraft,
    ContentItem,
    Device,
    DeviceAccount,
    PromptPreset,
    PublishTask,
    utcnow,
)
from app.schemas.dto import (
    AiConfigUpdate,
    ContentCreate,
    ContentUpdate,
    DraftAccept,
    DraftReject,
    DraftUpdate,
    GenerateDraftsRequest,
    PromptPresetUpsert,
    PromptUpdate,
)
from app.core.admin_auth import require_admin, require_admin_role
from app.core.admin_auth import Identity
from app.services import content_engine
from app.services.deepseek import DeepSeekError

router = APIRouter(prefix="/content", tags=["content"])


def serialize_content(
    item: ContentItem,
    device: Device | None = None,
    account_nickname: str | None = None,
) -> dict:
    data = item.model_dump()
    data["topics"] = json.loads(item.topics_json)
    data.pop("topics_json", None)
    data["published_device_name"] = device.name if device else None
    # Use the account matching THIS content's platform (an xhs post must show the
    # xhs nickname, not the device's douyin nickname). Fall back to douyin.
    data["published_device_nickname"] = account_nickname or (
        device.douyin_nickname if device else None
    )
    return normalize_datetimes(data)


@router.post("", status_code=201)
def create(payload: ContentCreate, session: Session = Depends(get_session)):
    item = ContentItem(
        cover_title=payload.cover_title,
        title=payload.title,
        body=payload.body,
        topics_json=json.dumps(payload.topics, ensure_ascii=False),
        city=payload.city.strip() or "通用",
        platform=payload.platform.strip() or "douyin",
        status=ContentStatus.PENDING,
        # 人工新建的内容没有草稿、也没有提示词版本 —— draft_id 和
        # prompt_version_id 保持为空，分析时正是靠这个把它和机器写的分开。
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return serialize_content(item)


@router.get("")
def list_content(
    status: str | None = None,
    city: str | None = None,
    session: Session = Depends(get_session),
):
    statement = select(ContentItem).order_by(ContentItem.created_at.desc())
    if status:
        statement = statement.where(ContentItem.status == status)
    if city:
        statement = statement.where(ContentItem.city == city)
    items = session.exec(statement).all()
    devices = {d.id: d for d in session.exec(select(Device)).all()}
    # (device_id, platform) -> account nickname, for platform-correct attribution.
    accounts = {
        (a.device_id, a.platform): a.nickname
        for a in session.exec(select(DeviceAccount)).all()
    }
    return [
        serialize_content(
            item,
            devices.get(item.published_device_id),
            accounts.get((item.published_device_id, item.platform)),
        )
        for item in items
    ]


@router.get("/cities")
def list_cities(session: Session = Depends(get_session)):
    """城市下拉选项：已接入账号所在的城市 + 内容库已用过的城市，去重排序。
    始终把「通用」「全国」放最前，方便草稿卡/城市筛选用同一份列表。"""
    cities: set[str] = set()
    for a in session.exec(select(DeviceAccount)).all():
        c = (a.city or "").strip()
        if c and c not in ("未分组", "通用", "全国"):
            cities.add(c)
    for c in session.exec(select(ContentItem.city).distinct()).all():
        c = (c or "").strip()
        if c and c not in ("未分组", "通用", "全国"):
            cities.add(c)
    return {"cities": ["通用", "全国", *sorted(cities)]}


@router.post("/{content_id}/return-to-pending", dependencies=[Depends(require_admin)])
def return_content_to_pending(
    content_id: int, session: Session = Depends(get_session)
):
    """内容库「发布中」→ 取消并退回待发布。Cancels any non-terminal task consuming this
    content (stops the agent + frees its device) AND force-resets the content to the
    pool — so it also fixes content STUCK in 发布中 whose task already died (陈旧数据)."""
    content = session.get(ContentItem, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="内容不存在")
    cancelled = 0
    for task in session.exec(
        select(PublishTask).where(PublishTask.content_id == content_id)
    ).all():
        if TaskStatus(task.status) in TERMINAL_STATUSES:
            continue
        task.status = TaskStatus.CANCELLED
        task.current_step = "cancelled"
        task.finished_at = utcnow()
        task.lease_expires_at = None
        task.updated_at = utcnow()
        if task.device_id:
            device = session.get(Device, task.device_id)
            if device:
                device.status = DeviceStatus.ONLINE
                device.current_task_id = None
                device.updated_at = utcnow()
                session.add(device)
        session.add(task)
        cancelled += 1
    # Force back to the pool (covers orphaned 发布中 with no live task too).
    content.status = ContentStatus.PENDING
    content.published_device_id = None
    content.published_task_id = None
    content.published_at = None
    content.updated_at = utcnow()
    session.add(content)
    session.commit()
    session.refresh(content)
    device = (
        session.get(Device, content.published_device_id)
        if content.published_device_id
        else None
    )
    return {"cancelled_tasks": cancelled, **serialize_content(content, device)}


@router.post("/import")
async def import_csv(
    file: UploadFile = File(...), session: Session = Depends(get_session)
):
    """Bulk-create content from a CSV file with columns:
    封面文字,正文,标题,话题,城市 (only 正文 is required)."""
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="文件为空或只有表头")
    created = 0
    skipped = 0
    for row in rows[1:]:  # drop header
        if not any(cell.strip() for cell in row):
            continue
        cells = (row + ["", "", "", "", ""])[:5]
        cover, body, title, topics_str, city = (c.strip() for c in cells)
        if not body:
            skipped += 1
            continue
        topics = [t for t in re.split(r"[ ,，#]+", topics_str) if t]
        session.add(
            ContentItem(
                cover_title=cover[:200],
                title=title[:100],
                body=body[:5000],
                topics_json=json.dumps(topics[:20], ensure_ascii=False),
                city=city or "通用",
                status=ContentStatus.PENDING,
            )
        )
        created += 1
    session.commit()
    return {"created": created, "skipped": skipped}


def serialize_draft(draft: ContentDraft) -> dict:
    data = draft.model_dump()
    data["topics"] = json.loads(draft.topics_json or "[]")
    data.pop("topics_json", None)
    return normalize_datetimes(data)


@router.get("/engine/status")
def engine_status(session: Session = Depends(get_session)):
    """Whether the DeepSeek 内容引擎 is configured (admin-saved key or env)."""
    return {"configured": content_engine.is_configured(session)}


@router.get("/engine/aiconfig", dependencies=[Depends(require_admin_role)])
def get_ai_config(session: Session = Depends(get_session)):
    """管理员限定：AI 提供方配置（密钥只回掩码）。可指向企业网关(OpenAI 兼容)。
    operator 会 403。"""
    key = content_engine.resolve_api_key(session)
    masked = ""
    if key:
        masked = key[:4] + "…" + key[-4:] if len(key) > 10 else "已配置"
    from app.models.entities import AppSetting

    krow = session.get(AppSetting, content_engine.DEEPSEEK_KEY_SETTING)
    return {
        "configured": bool(key),
        "masked": masked,
        "source": "custom" if (krow and krow.value.strip()) else ("env" if key else "none"),
        "base_url": content_engine.resolve_base_url(session),
        "model": content_engine.resolve_model(session),
    }


@router.put("/engine/aiconfig", dependencies=[Depends(require_admin_role)])
def put_ai_config(payload: AiConfigUpdate, session: Session = Depends(get_session)):
    """管理员限定：保存 AI 配置（密钥/网关地址/模型；空串=清除回退环境变量）。"""
    content_engine.set_ai_config(
        session,
        api_key=payload.api_key,
        base_url=payload.base_url,
        model=payload.model,
    )
    return get_ai_config(session)


@router.get("/engine/prompt")
def get_prompt(session: Session = Depends(get_session)):
    """The current (editable) system prompt + the built-in default for reference."""
    current = content_engine.get_system_prompt(session)
    return {
        "prompt": current,
        "default": content_engine.DEFAULT_SYSTEM,
        "is_custom": current != content_engine.DEFAULT_SYSTEM,
    }


@router.put("/engine/prompt")
def put_prompt(payload: PromptUpdate, session: Session = Depends(get_session)):
    content_engine.set_system_prompt(session, payload.prompt)
    return get_prompt(session)


@router.get("/engine/preview")
def preview(
    count: int = 3,
    platform: str = "xhs",
    theme: str | None = None,
    preset_id: int | None = None,
    session: Session = Depends(get_session),
):
    """Exactly what gets sent to the model (system + assembled user message)."""
    return content_engine.preview_prompt(
        session, count=count, platform=platform,
        theme=(theme or "").strip() or None, preset_id=preset_id,
    )


@router.post("/generate")
def generate(payload: GenerateDraftsRequest, session: Session = Depends(get_session)):
    """二改: remix our top-performing posts into AI drafts (pending review)."""
    try:
        drafts = content_engine.generate_drafts(
            session,
            count=payload.count,
            platform=payload.platform.strip() or "xhs",
            theme=(payload.theme or "").strip() or None,
            source_content_ids=payload.source_content_ids or None,
            preset_id=payload.preset_id,
        )
    except DeepSeekError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [serialize_draft(d) for d in drafts]


def serialize_preset(p: PromptPreset) -> dict:
    data = p.model_dump()
    data["sample_ids"] = json.loads(p.sample_ids_json or "[]")
    data.pop("sample_ids_json", None)
    return normalize_datetimes(data)


@router.get("/engine/presets")
def list_presets(
    identity: "Identity | None" = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Presets visible to the caller: their own + shared (owner="")."""
    me = identity.name if identity else ""
    rows = session.exec(select(PromptPreset).order_by(PromptPreset.updated_at.desc())).all()
    return [serialize_preset(p) for p in rows if p.owner in ("", me)]


@router.post("/engine/presets", status_code=201)
def create_preset(
    payload: PromptPresetUpsert,
    identity: "Identity | None" = Depends(require_admin),
    session: Session = Depends(get_session),
):
    p = PromptPreset(
        name=payload.name.strip() or "未命名预设",
        owner=(identity.name if identity else ""),
        system_prompt=payload.system_prompt,
        sample_mode=payload.sample_mode if payload.sample_mode in ("default", "custom", "pick") else "default",
        sample_text=payload.sample_text,
        sample_ids_json=json.dumps(payload.sample_ids, ensure_ascii=False),
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return serialize_preset(p)


@router.put("/engine/presets/{preset_id}")
def update_preset(
    preset_id: int,
    payload: PromptPresetUpsert,
    identity: "Identity | None" = Depends(require_admin),
    session: Session = Depends(get_session),
):
    p = session.get(PromptPreset, preset_id)
    if not p:
        raise HTTPException(status_code=404, detail="预设不存在")
    me = identity.name if identity else ""
    if p.owner not in ("", me):
        raise HTTPException(status_code=403, detail="只能改自己的预设")
    p.name = payload.name.strip() or p.name
    p.system_prompt = payload.system_prompt
    p.sample_mode = payload.sample_mode if payload.sample_mode in ("default", "custom", "pick") else "default"
    p.sample_text = payload.sample_text
    p.sample_ids_json = json.dumps(payload.sample_ids, ensure_ascii=False)
    p.updated_at = utcnow()
    session.add(p)
    session.commit()
    session.refresh(p)
    return serialize_preset(p)


@router.delete("/engine/presets/{preset_id}", status_code=204)
def delete_preset(
    preset_id: int,
    identity: "Identity | None" = Depends(require_admin),
    session: Session = Depends(get_session),
):
    p = session.get(PromptPreset, preset_id)
    if not p:
        return
    me = identity.name if identity else ""
    if p.owner not in ("", me):
        raise HTTPException(status_code=403, detail="只能删自己的预设")
    session.delete(p)
    session.commit()


@router.get("/drafts")
def list_drafts(
    status: str | None = "pending", session: Session = Depends(get_session)
):
    statement = select(ContentDraft).order_by(ContentDraft.created_at.desc())
    if status:
        statement = statement.where(ContentDraft.status == status)
    return [serialize_draft(d) for d in session.exec(statement).all()]


@router.get("/drafts/stats")
def draft_stats(session: Session = Depends(get_session)):
    """二改成本与查重概览：累计 token / 人民币成本 + 各状态草稿数。"""
    rows = session.exec(select(ContentDraft)).all()
    by_status: dict[str, int] = {}
    for d in rows:
        by_status[d.status] = by_status.get(d.status, 0) + 1
    return {
        "total_drafts": len(rows),
        "by_status": by_status,
        "duplicate": by_status.get("duplicate", 0),
        "prompt_tokens": sum(d.prompt_tokens for d in rows),
        "completion_tokens": sum(d.completion_tokens for d in rows),
        "cost_cny": round(sum(d.cost_cny for d in rows), 4),
    }


@router.patch("/drafts/{draft_id}")
def update_draft(
    draft_id: int, payload: DraftUpdate, session: Session = Depends(get_session)
):
    draft = session.get(ContentDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")
    if payload.cover_title is not None:
        draft.cover_title = payload.cover_title
    if payload.title is not None:
        draft.title = payload.title
    if payload.body is not None:
        # 老草稿（这个字段上线之前生成的）original_body 是空的，第一次被改
        # 之前把当时的正文存下来。新草稿落库时就存好了，这里不会覆盖。
        if not draft.original_body:
            draft.original_body = draft.body
        draft.body = payload.body
    if payload.topics is not None:
        draft.topics_json = json.dumps(payload.topics, ensure_ascii=False)
    if payload.platform is not None:
        draft.platform = payload.platform.strip() or draft.platform
    draft.updated_at = utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return serialize_draft(draft)


@router.post("/drafts/{draft_id}/accept", status_code=201)
def accept_draft(
    draft_id: int, payload: DraftAccept, session: Session = Depends(get_session)
):
    """Accept an AI draft into the content pool as a real (pending) ContentItem."""
    draft = session.get(ContentDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")
    if draft.status == "accepted" and draft.accepted_content_id:
        existing = session.get(ContentItem, draft.accepted_content_id)
        if existing:
            return serialize_content(existing)
    item = ContentItem(
        cover_title=draft.cover_title,
        title=draft.title,
        body=draft.body,
        topics_json=draft.topics_json,
        city=payload.city.strip() or "通用",
        platform=draft.platform,
        status=ContentStatus.PENDING,
        # 溯源：这条内容是哪条草稿、哪版提示词来的
        draft_id=draft.id,
        prompt_version_id=draft.prompt_version_id,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    # Serialize BEFORE the next commit — committing the draft update expires the
    # `item` instance, which would make model_dump() come back empty.
    result = serialize_content(item)
    draft.status = "accepted"
    draft.accepted_content_id = item.id
    draft.updated_at = utcnow()
    session.add(draft)
    session.commit()
    return result


@router.post("/drafts/{draft_id}/reject")
def reject_draft(
    draft_id: int,
    payload: DraftReject | None = None,
    session: Session = Depends(get_session),
):
    draft = session.get(ContentDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")
    draft.status = "rejected"
    # 理由可空（前端老版本不传），但传了就收下 —— 它是"模型老犯什么错"的唯一来源
    if payload and payload.reason.strip():
        draft.reject_reason = payload.reason.strip()[:200]
    draft.updated_at = utcnow()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return serialize_draft(draft)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(draft_id: int, session: Session = Depends(get_session)):
    draft = session.get(ContentDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")
    session.delete(draft)
    session.commit()


@router.get("/performance/summary")
def performance_summary(session: Session = Depends(get_session)):
    """「哪种写法更好」的一句话结论。

    ⚠ 单独一个接口，不塞进 /performance —— 那个接口是先排序再截断的，
    前端拿它的返回自己算，算出来的是高分子集的倍数，不是全量的。
    """
    from app.services.metrics import type_summary

    return type_summary(session)


@router.get("/performance")
def performance(
    limit: int = Query(1000, ge=1, le=5000),
    session: Session = Depends(get_session),
):
    """效果榜: latest 回采 metrics per content, ranked by engagement.

    limit 以前写死在服务层的 100，已发布 900+ 篇时榜单被永久截断（新内容互动低，
    永远进不了前 100），看板上的数字几个月不变。"""
    from app.services.metrics import content_performance

    return normalize_datetimes(content_performance(session, limit))


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    rows = session.exec(
        select(ContentItem.status, func.count()).group_by(ContentItem.status)
    ).all()
    counts = {status: count for status, count in rows}
    return {
        "pending": counts.get(ContentStatus.PENDING, 0),
        "publishing": counts.get(ContentStatus.PUBLISHING, 0),
        "published": counts.get(ContentStatus.PUBLISHED, 0),
        "disabled": counts.get(ContentStatus.DISABLED, 0),
        "total": sum(counts.values()),
    }


@router.patch("/{content_id}")
def update(
    content_id: int,
    payload: ContentUpdate,
    session: Session = Depends(get_session),
):
    item = session.get(ContentItem, content_id)
    if not item:
        raise HTTPException(status_code=404, detail="内容不存在")
    if payload.cover_title is not None:
        item.cover_title = payload.cover_title
    if payload.title is not None:
        item.title = payload.title
    if payload.body is not None:
        item.body = payload.body
    if payload.topics is not None:
        item.topics_json = json.dumps(payload.topics, ensure_ascii=False)
    if payload.city is not None:
        item.city = payload.city.strip() or "通用"
    if payload.platform is not None:
        item.platform = payload.platform.strip() or "douyin"
    if payload.status is not None:
        item.status = payload.status
    item.updated_at = utcnow()
    session.add(item)
    session.commit()
    session.refresh(item)
    return serialize_content(item)


@router.delete("/{content_id}", status_code=204)
def delete(content_id: int, session: Session = Depends(get_session)):
    item = session.get(ContentItem, content_id)
    if not item:
        raise HTTPException(status_code=404, detail="内容不存在")
    session.delete(item)
    session.commit()

@router.get("/publish-target")
def get_publish_target(session: Session = Depends(get_session)):
    """每个号每天**期望**发几篇（默认 1）。

    注意和账号上的 `daily_quota` 区别：quota 是**上限**，这里是**目标**。
    算内容库水位、日报、自动发布节奏都用这个值 —— 用上限算会让水位显得
    紧张一倍，天天报假的「内容告急」。
    """
    from app.services.notify_sweep import DEFAULT_DAILY_TARGET, daily_target

    return {"daily_target": daily_target(session), "default": DEFAULT_DAILY_TARGET}


@router.put("/publish-target", dependencies=[Depends(require_admin_role)])
def set_publish_target(
    value: int = Query(ge=1, le=20), session: Session = Depends(get_session)
):
    """设「每个账号每天发几篇」。

    ⚠ **同时把每个账号的 daily_quota 也提到这个值**，否则这个设置是假的：
    实际篇数 = `min(全局目标, 单号上限)`（notify_sweep._expected_for），
    而 daily_quota 的默认值是 2、界面上又没有任何地方能改它 ——
    运营设成 3 篇，页面却显示「1 / 2」，而且不告诉他为什么。
    （用户实测反馈：「我设置了每天发布3篇，但是上面才显示1/2」。）

    界面上只有这一个控件，所以就让它说了算。将来真要做单号覆盖，
    再加一个per-account 编辑器，这里改成"全部设为 N"即可。
    """
    from app.models.entities import DeviceAccount
    from app.services import settings_store
    from app.services.notify_sweep import DAILY_TARGET_KEY

    settings_store.put(session, DAILY_TARGET_KEY, str(value))
    changed = 0
    for account in session.exec(select(DeviceAccount)).all():
        if account.daily_quota != value:
            account.daily_quota = value
            session.add(account)
            changed += 1
    if changed:
        session.commit()
    return {"daily_target": value, "accounts_updated": changed}
