"""内容引擎 (Phase B) — DeepSeek call is mocked so the generate→review→accept
pipeline is verified without spending tokens / needing balance."""

from fastapi.testclient import TestClient

from app.main import app
from app.services import deepseek

_FAKE = {
    "drafts": [
        {
            "cover_title": "封面A",
            "title": "标题A",
            "body": "正文A，有钩子。大家怎么选？",
            "topics": ["上海买房", "#砍价"],
        },
        {
            "cover_title": "封面B",
            "title": "",
            "body": "正文B",
            "topics": ["二手房"],
        },
    ]
}


def test_generate_review_accept(monkeypatch):
    _usage = {"prompt_tokens": 1200, "completion_tokens": 800, "total_tokens": 2000}
    monkeypatch.setattr(deepseek, "chat_json", lambda *a, **k: (_FAKE, _usage))
    with TestClient(app) as client:
        gen = client.post(
            "/api/v1/content/generate", json={"count": 2, "platform": "xhs"}
        )
        assert gen.status_code == 200, gen.text
        drafts = gen.json()
        assert len(drafts) == 2
        assert drafts[0]["status"] == "pending"
        assert drafts[0]["topics"] == ["上海买房", "砍价"]  # '#' stripped

        # pending list contains them
        pending = client.get("/api/v1/content/drafts").json()
        ids = {d["id"] for d in pending}
        assert drafts[0]["id"] in ids and drafts[1]["id"] in ids

        # cost accounting: batch usage split across the 2 drafts (1200/2, 800/2)
        assert drafts[0]["prompt_tokens"] == 600
        assert drafts[0]["completion_tokens"] == 400
        assert drafts[0]["cost_cny"] > 0
        stats = client.get("/api/v1/content/drafts/stats").json()
        assert stats["prompt_tokens"] >= 1200 and stats["cost_cny"] > 0

        # accept the first → becomes a real ContentItem
        acc = client.post(
            f"/api/v1/content/drafts/{drafts[0]['id']}/accept",
            json={"city": "上海"},
        )
        assert acc.status_code == 201, acc.text
        item = acc.json()
        assert item["body"] == "正文A，有钩子。大家怎么选？"
        assert item["city"] == "上海"
        assert item["platform"] == "xhs"
        assert item["status"] == "pending"

        # the content pool now has it
        pool = client.get("/api/v1/content").json()
        assert any(c["id"] == item["id"] for c in pool)

        # accepted draft drops out of the pending list
        pending2 = client.get("/api/v1/content/drafts").json()
        assert drafts[0]["id"] not in {d["id"] for d in pending2}

        # reject the second
        rej = client.post(f"/api/v1/content/drafts/{drafts[1]['id']}/reject")
        assert rej.status_code == 200
        assert rej.json()["status"] == "rejected"


def test_generate_flags_duplicate(monkeypatch):
    """A draft whose body matches an existing draft/content is marked duplicate
    and drops out of the pending review queue."""
    usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
    dup_body = "完全一样的正文内容，用来触发查重逻辑，越长越稳。" * 3
    fake = {"drafts": [{"cover_title": "原创", "title": "", "body": dup_body, "topics": []}]}
    fake_again = {"drafts": [{"cover_title": "抄的", "title": "", "body": dup_body, "topics": []}]}
    with TestClient(app) as client:
        monkeypatch.setattr(deepseek, "chat_json", lambda *a, **k: (fake, usage))
        first = client.post("/api/v1/content/generate", json={"count": 1, "platform": "xhs"}).json()
        assert first[0]["status"] == "pending"
        # Second batch with the SAME body → caught by 查重.
        monkeypatch.setattr(deepseek, "chat_json", lambda *a, **k: (fake_again, usage))
        second = client.post("/api/v1/content/generate", json={"count": 1, "platform": "xhs"}).json()
        assert second[0]["status"] == "duplicate"
        assert second[0]["dup_score"] >= 0.9
        assert second[0]["dup_of"]  # names what it duplicated
        # duplicate is hidden from the default (pending) review list
        pending_ids = {d["id"] for d in client.get("/api/v1/content/drafts").json()}
        assert second[0]["id"] not in pending_ids


def test_generate_without_key_returns_400():
    # Test env has no DEEPSEEK_API_KEY → clear 400, not a 500.
    with TestClient(app) as client:
        r = client.post("/api/v1/content/generate", json={"count": 1})
        assert r.status_code == 400
        assert "密钥" in r.json()["detail"]  # 未配置 API 密钥 …


def test_engine_status():
    with TestClient(app) as client:
        r = client.get("/api/v1/content/engine/status")
        assert r.status_code == 200
        assert r.json() == {"configured": False}


def test_engine_prompt_edit_and_reset():
    with TestClient(app) as client:
        base = client.get("/api/v1/content/engine/prompt").json()
        assert base["is_custom"] is False
        assert base["prompt"] == base["default"]

        saved = client.put(
            "/api/v1/content/engine/prompt", json={"prompt": "只写一句话测试"}
        ).json()
        assert saved["is_custom"] is True
        assert saved["prompt"] == "只写一句话测试"
        assert client.get("/api/v1/content/engine/prompt").json()["prompt"] == "只写一句话测试"

        # blank resets to default
        reset = client.put("/api/v1/content/engine/prompt", json={"prompt": ""}).json()
        assert reset["is_custom"] is False
        assert reset["prompt"] == reset["default"]


def test_engine_preview_shows_assembled_input(monkeypatch):
    with TestClient(app) as client:
        r = client.get("/api/v1/content/engine/preview?platform=xhs&count=2")
        assert r.status_code == 200
        body = r.json()
        assert "system" in body and "user" in body
        assert "2 篇" in body["user"]
