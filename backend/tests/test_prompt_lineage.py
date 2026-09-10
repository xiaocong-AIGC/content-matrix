"""提示词溯源：每条内容都要能回答「你是哪版提示词写的」。

这是盘点出的 13 处断点里**唯一时间不可逆**的一处 —— 没记下来的那些，
事后补不回来。按每周约 94 条真稿算，晚上线一周就少 94 条可用样本。

版本按正文的 sha256 寻址，不是「版本号 + 活动指针」：这张表要回答的是
「真正发出去的那段话是什么」，指针记的是「谁声明当前用哪版」，会漂开。
"""

import uuid

from sqlmodel import Session, select

from app.db.session import create_db_and_tables, engine
from app.models.entities import ContentDraft, PromptVersion
from app.services.content_engine import record_prompt_version


def _cleanup(session: Session, *rows):
    for row in rows:
        if row is not None:
            obj = session.get(type(row), row.id)
            if obj is not None:
                session.delete(obj)
    session.commit()


def test_same_text_is_one_version_different_text_is_a_new_one():
    create_db_and_tables()
    with Session(engine) as session:
        tag = uuid.uuid4().hex[:8]
        text_a = f"你是资深操盘手。规则甲。{tag}"
        text_b = f"你是资深操盘手。规则乙。{tag}"
        v1 = record_prompt_version(session, text_a)
        v2 = record_prompt_version(session, text_a)      # 同一段，第二次
        v3 = record_prompt_version(session, text_b)      # 改了一个字
        try:
            assert v1 and v2 and v3
            assert v1.id == v2.id, "同一段提示词不该产生两行"
            assert v3.id != v1.id, "改过的提示词必须是新的一行"
            assert v1.sha256 != v3.sha256
        finally:
            _cleanup(session, v1, v3)


def test_whitespace_only_difference_is_the_same_version():
    """前后空白不算改动 —— 否则运营在文本框里多敲一个回车就多一版，
    版本表会被噪声撑满，而「这两版有什么不同」看不出任何区别。"""
    create_db_and_tables()
    with Session(engine) as session:
        tag = uuid.uuid4().hex[:8]
        v1 = record_prompt_version(session, f"规则文本 {tag}")
        v2 = record_prompt_version(session, f"  规则文本 {tag}\n\n")
        try:
            assert v1 and v2 and v1.id == v2.id
        finally:
            _cleanup(session, v1)


def test_empty_prompt_records_nothing():
    """没发生过的事不记账。"""
    create_db_and_tables()
    with Session(engine) as session:
        assert record_prompt_version(session, "") is None
        assert record_prompt_version(session, "   \n ") is None


def test_draft_carries_the_version_and_its_untouched_original():
    """草稿落库时就带上版本和原文；人改过之后 original_body 不变。"""
    create_db_and_tables()
    with Session(engine) as session:
        tag = uuid.uuid4().hex[:8]
        version = record_prompt_version(session, f"某一版提示词 {tag}")
        draft = ContentDraft(
            cover_title="封面", title="标题", body="模型写的原文",
            platform="douyin", city="深圳",
            prompt_version_id=version.id, original_body="模型写的原文",
        )
        session.add(draft)
        session.commit()
        session.refresh(draft)
        try:
            # 运营改了正文
            draft.body = "人改过的正文"
            session.add(draft)
            session.commit()
            session.refresh(draft)

            assert draft.prompt_version_id == version.id
            assert draft.original_body == "模型写的原文"
            assert draft.body != draft.original_body
            # 拿 original_body 才能评价提示词本身；拿 body 评价的是「模型+人」
            back = session.exec(
                select(PromptVersion).where(
                    PromptVersion.id == draft.prompt_version_id
                )
            ).first()
            assert back and back.text.endswith(tag)
        finally:
            _cleanup(session, draft, version)
