"""内容引擎 (Phase B): analyse our own high-engagement posts and remix their
STRUCTURE into fresh, differentiated drafts via DeepSeek — not blank generation.

Pipeline: pick exemplars (top content by 回采 engagement, or explicit ids) →
build a prompt that extracts the winning structure and asks for K differentiated
variants → DeepSeek JSON → store as ContentDraft rows for human review.
"""

import json
import re
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.entities import AppSetting, ContentDraft, ContentItem, PromptPreset
from app.services import deepseek
from app.services.metrics import top_posts_for_remix

PROMPT_KEY = "engine_system_prompt"

# 查重阈值：>= DUP_REJECT 直接标记为 duplicate（不入待审），>= DUP_WARN 仅标注疑似。
DUP_REJECT = 0.90
DUP_WARN = 0.75


def _norm(text: str) -> str:
    """Normalise body text for similarity: drop whitespace/punctuation/emoji so
    cosmetic edits don't hide a near-duplicate."""
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _similar(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _dup_corpus(session: Session) -> list[tuple[str, str]]:
    """(body, label) of existing pool content + non-rejected drafts to check against."""
    corpus: list[tuple[str, str]] = []
    for item in session.exec(
        select(ContentItem).where(ContentItem.body != "").limit(400)
    ).all():
        corpus.append((item.body, f"内容#{item.id} {item.cover_title or item.title}"[:120]))
    for d in session.exec(
        select(ContentDraft)
        .where(ContentDraft.body != "", ContentDraft.status != "rejected")
        .limit(400)
    ).all():
        corpus.append((d.body, f"草稿#{d.id} {d.cover_title or d.title}"[:120]))
    return corpus


def _best_dup(body: str, corpus: list[tuple[str, str]]) -> tuple[float, str]:
    best, label = 0.0, ""
    for other, lbl in corpus:
        s = _similar(body, other)
        if s > best:
            best, label = s, lbl
    return best, label


# Admin-editable AI provider config (AppSetting overrides the env). Operators
# never see it; only 管理员 via /content/engine/aiconfig. Key = the API key,
# base_url = any OpenAI-compatible endpoint (a public cloud one, or a company
# gateway on the intranet), model = the model id.
#
# ⚠ 这个仓库是**公开**的：具体的内网网关域名、模型 id、密钥一律不写进代码和
# 界面文案，只放 backend/.env（已 gitignore）或管理员在界面上填。
DEEPSEEK_KEY_SETTING = "deepseek_api_key"
DEEPSEEK_URL_SETTING = "deepseek_base_url"
DEEPSEEK_MODEL_SETTING = "deepseek_model"


def _setting(session: Session, key: str) -> str:
    row = session.get(AppSetting, key)
    return row.value.strip() if row and row.value.strip() else ""


def resolve_api_key(session: Session) -> str:
    return _setting(session, DEEPSEEK_KEY_SETTING) or get_settings().deepseek_api_key


def resolve_base_url(session: Session) -> str:
    return _setting(session, DEEPSEEK_URL_SETTING) or get_settings().deepseek_base_url


def resolve_model(session: Session) -> str:
    return _setting(session, DEEPSEEK_MODEL_SETTING) or get_settings().deepseek_model


def is_configured(session: Session) -> bool:
    return bool(resolve_api_key(session))


def _put_setting(session: Session, key: str, value: str) -> None:
    from app.models.entities import utcnow

    row = session.get(AppSetting, key)
    if row:
        row.value = value.strip()
        row.updated_at = utcnow()
    else:
        row = AppSetting(key=key, value=value.strip())
    session.add(row)


def set_api_key(session: Session, value: str) -> None:
    _put_setting(session, DEEPSEEK_KEY_SETTING, value)
    session.commit()


def set_ai_config(
    session: Session, *, api_key: str | None, base_url: str | None, model: str | None
) -> None:
    """Save AI provider config. Only non-None fields are updated (so you can
    change the gateway URL without re-entering the key). Empty string clears a
    field (falls back to env)."""
    if api_key is not None:
        _put_setting(session, DEEPSEEK_KEY_SETTING, api_key)
    if base_url is not None:
        _put_setting(session, DEEPSEEK_URL_SETTING, base_url)
    if model is not None:
        _put_setting(session, DEEPSEEK_MODEL_SETTING, model)
    session.commit()

DEFAULT_SYSTEM = (
    "你是资深的小红书/抖音图文爆款操盘手。你的任务是：先分析给定的高互动样本"
    "文案的结构（开头钩子、痛点切入、信息密度、情绪、结尾互动引导、话题选择），"
    "再据此创作若干篇【同领域但内容差异化】的新文案——不是改写同一篇，而是换"
    "角度、换场景、换具体数字，保持能打的结构。\n"
    "字段要求（务必区分）：\n"
    "• cover_title = 大字报封面文案，【80字以内】，可以是一句也可以是几句话，要有"
    "冲击力——用疑问/冲突/反常识/强代入感的口语，像真人在吐槽或提问（参考样本封面"
    "的写法，例如『男方在上海有房但不肯加我的名，还能嫁吗？我感觉没有安全感』『听说"
    "汪汪队还在回收老破小，难怪300万左右的房子成交量这么大，会真的把房价带起来吗？"
    "我还没上车呢』）。\n"
    "• title = 发布页标题，精炼【20字以内】。\n"
    "• body = 完整正文，开头有钩子即可，像活人发的碎碎念——口语、真实、有点情绪和"
    "细节，有信息增量；不要刻意加『你怎么看/评论区见/欢迎讨论』这类结尾互动套话。\n"
    "• topics = 3-6个话题，不带#。\n"
    "严禁虚构夸大承诺、严禁雷同。"
)


def get_system_prompt(session: Session) -> str:
    """The operator-editable system prompt (falls back to DEFAULT_SYSTEM)."""
    row = session.get(AppSetting, PROMPT_KEY)
    return row.value if row and row.value.strip() else DEFAULT_SYSTEM


def set_system_prompt(session: Session, value: str) -> None:
    """Persist a custom system prompt; blank value resets to the default."""
    from app.models.entities import utcnow

    row = session.get(AppSetting, PROMPT_KEY)
    if not value.strip():
        if row:
            session.delete(row)
            session.commit()
        return
    if row:
        row.value = value
        row.updated_at = utcnow()
    else:
        row = AppSetting(key=PROMPT_KEY, value=value)
    session.add(row)
    session.commit()


# 喂给模型的样本条数。原来写死 5 条，运营反馈"太少、学不到东西"；线上按账号城市
# 算每个城市有 100~420 条可选样本（上海420/深圳224/北京100），10 条完全取得出来。
# 注意：样本进的是 prompt，条数×正文长度会推高输入 token，max_tokens 已按 count 放大。
EXEMPLAR_LIMIT = 10


def _exemplars(
    session: Session, platform: str, source_content_ids: list[int] | None
) -> list[dict]:
    """Return up to EXEMPLAR_LIMIT exemplar posts (cover/body/topics + engagement),
    best first.
    Priority: explicit ids → 回采ed posts with a captured body ranked by engagement
    (incl. unmatched, often our best) → recent library content with a body."""
    exemplars: list[dict] = []
    if source_content_ids:
        for cid in source_content_ids[:EXEMPLAR_LIMIT]:
            item = session.get(ContentItem, cid)
            if item:
                exemplars.append(
                    {
                        "content_id": item.id,
                        "cover_title": item.cover_title,
                        "body": item.body,
                        "topics": json.loads(item.topics_json or "[]"),
                        "metrics": None,
                    }
                )
        return exemplars

    for row in top_posts_for_remix(session, platform, limit=EXEMPLAR_LIMIT):
        exemplars.append(
            {
                "content_id": row["content_id"],
                "cover_title": row["title"],
                "body": row["body"],
                "topics": [],
                "metrics": f"浏览{row['views']} 赞{row['likes']} 藏{row['collects']} 评{row['comments']}",
            }
        )
    if exemplars:
        return exemplars

    # Cold start: no 回采 with body yet — fall back to recent library content,
    # but SAME-PLATFORM only (else generating for 抖音 fed 小红书 样本 → “样本完全错误”).
    q = select(ContentItem).where(ContentItem.body != "")
    if platform and platform != "both":
        q = q.where(ContentItem.platform.in_([platform, "both"]))
    for item in session.exec(
        q.order_by(ContentItem.created_at.desc()).limit(EXEMPLAR_LIMIT)
    ).all():
        exemplars.append(
            {
                "content_id": item.id,
                "cover_title": item.cover_title,
                "body": item.body,
                "topics": json.loads(item.topics_json or "[]"),
                "metrics": None,
            }
        )
    return exemplars


def _build_user_prompt(
    exemplars: list[dict],
    count: int,
    platform: str,
    theme: str | None,
    raw_samples: str | None = None,
) -> str:
    plat = {"xhs": "小红书", "douyin": "抖音", "both": "小红书+抖音"}.get(platform, platform)
    lines = [f"目标平台：{plat}。请创作 {count} 篇差异化新文案。"]
    if theme:
        lines.append(f"本次选题方向（务必围绕）：{theme}")
    if raw_samples and raw_samples.strip():
        # 运营自定义样本（预设里粘贴/上传的）——原样作为借鉴对象。
        lines.append("\n以下是需要借鉴其结构/风格的样本：")
        lines.append(raw_samples.strip())
    else:
        lines.append("\n以下是我们已发布、数据较好的样本（含互动数据，越高越值得借鉴其结构）：")
        for i, ex in enumerate(exemplars, 1):
            lines.append(f"\n样本{i}（{ex.get('metrics') or '无回采数据'}）：")
            lines.append(f"  封面：{ex['cover_title']}")
            lines.append(f"  正文：{ex['body']}")
            if ex["topics"]:
                lines.append(f"  话题：{' '.join(ex['topics'])}")
    lines.append(
        "\n只输出 JSON，格式：{\"drafts\":[{\"cover_title\":\"\",\"title\":\"\","
        "\"body\":\"\",\"topics\":[\"\"],\"city\":\"\"}]}。"
        "其中 city = 根据这篇文案内容判断的目标城市（如上海、杭州、成都；若为无特定城市的"
        "通用内容则填『通用』）。"
        f"drafts 长度必须等于 {count}。"
    )
    return "\n".join(lines)


def _resolve_preset(session: Session, platform: str, preset_id: int | None):
    """Resolve (system_prompt, exemplars, raw_samples) from a saved preset.
    None preset → current default behaviour (editable system prompt + top-performers)."""
    if preset_id is not None:
        preset = session.get(PromptPreset, preset_id)
        if preset:
            system = preset.system_prompt.strip() or DEFAULT_SYSTEM
            if preset.sample_mode == "custom":
                return system, [], preset.sample_text
            if preset.sample_mode == "pick":
                ids = json.loads(preset.sample_ids_json or "[]")
                return system, _exemplars(session, platform, ids or None), None
            return system, _exemplars(session, platform, None), None  # default
    return get_system_prompt(session), _exemplars(session, platform, None), None


def preview_prompt(
    session: Session,
    *,
    count: int = 3,
    platform: str = "xhs",
    theme: str | None = None,
    preset_id: int | None = None,
) -> dict:
    """Return exactly what would be sent to the model (system + assembled user
    message with the current exemplars), so the operator can inspect/tune it."""
    system, exemplars, raw = _resolve_preset(session, platform, preset_id)
    return {
        "system": system,
        "user": _build_user_prompt(exemplars, count, platform, theme, raw),
        "exemplar_count": len(exemplars) if not raw else 0,
    }


def generate_drafts(
    session: Session,
    *,
    count: int = 3,
    platform: str = "xhs",
    theme: str | None = None,
    source_content_ids: list[int] | None = None,
    preset_id: int | None = None,
) -> list[ContentDraft]:
    """Generate `count` AI drafts and persist them as pending ContentDraft rows."""
    count = max(1, min(count, 10))
    # An explicit preset wins; else legacy source_content_ids; else default logic.
    if preset_id is not None:
        system, exemplars, raw_samples = _resolve_preset(session, platform, preset_id)
    else:
        system = get_system_prompt(session)
        exemplars = _exemplars(session, platform, source_content_ids)
        raw_samples = None
    user = _build_user_prompt(exemplars, count, platform, theme, raw_samples)
    # 推理模型先写 reasoning 再写正文，额度不足会导致正文为空（见 deepseek.chat_json）。
    # 按条数放大：思考开销 + 每条约 1200 token 的正文余量。
    result, usage = deepseek.chat_json(
        system,
        user,
        api_key=resolve_api_key(session),
        base_url=resolve_base_url(session),
        model=resolve_model(session),
        max_tokens=min(32000, 6000 + count * 1500),
    )
    raw_drafts = result.get("drafts") if isinstance(result, dict) else None
    if not isinstance(raw_drafts, list) or not raw_drafts:
        from app.services.deepseek import DeepSeekError

        raise DeepSeekError("DeepSeek 未返回有效的 drafts 列表")
    source_id = exemplars[0]["content_id"] if exemplars else None
    source_note = "; ".join(
        f"{ex.get('cover_title') or ('#' + str(ex['content_id']))}"
        f"({ex.get('metrics') or '无数据'})"
        for ex in exemplars
    )
    settings = get_settings()
    model = settings.deepseek_model
    # 整批一次 API 调用 → 把 token 与成本平摊到每条草稿，便于在库里逐条/汇总统计。
    valid = [d for d in raw_drafts[:count] if isinstance(d, dict)]
    n = max(1, len(valid))
    cost_total = (
        usage["prompt_tokens"] / 1_000_000 * settings.deepseek_price_in
        + usage["completion_tokens"] / 1_000_000 * settings.deepseek_price_out
    )
    per_prompt = usage["prompt_tokens"] // n
    per_completion = usage["completion_tokens"] // n
    per_cost = round(cost_total / n, 6)

    corpus = _dup_corpus(session)  # snapshot of existing content+drafts to check against
    drafts: list[ContentDraft] = []
    for d in valid:
        topics = [str(t).strip().removeprefix("#") for t in (d.get("topics") or []) if str(t).strip()]
        body = str(d.get("body", ""))[:5000]
        # AI 按内容判断的目标城市（人工采纳时可在草稿卡下拉里改）。
        city = (str(d.get("city", "") or "").strip()[:40]) or "通用"
        # Check against existing AND the drafts produced earlier in THIS batch.
        score, label = _best_dup(body, corpus + [(x.body, f"本批 {x.cover_title}") for x in drafts])
        status = "duplicate" if score >= DUP_REJECT else "pending"
        draft = ContentDraft(
            cover_title=str(d.get("cover_title", ""))[:200],
            title=str(d.get("title", ""))[:100],
            body=body,
            topics_json=json.dumps(topics[:20], ensure_ascii=False),
            platform=platform,
            city=city,
            source_content_id=source_id,
            source_note=source_note[:1000],
            model=model,
            status=status,
            prompt_tokens=per_prompt,
            completion_tokens=per_completion,
            cost_cny=per_cost,
            dup_score=round(score, 4),
            dup_of=label if score >= DUP_WARN else "",
        )
        session.add(draft)
        drafts.append(draft)
    session.commit()
    for draft in drafts:
        session.refresh(draft)
    return drafts
