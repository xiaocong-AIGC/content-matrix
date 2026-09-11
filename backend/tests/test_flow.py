from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app


def _register_device(client: TestClient, code: str, name: str) -> dict:
    return client.post(
        "/api/v1/devices/register",
        json={"device_code": code, "name": name, "capabilities": ["task_pull"]},
    ).json()


def test_minimum_task_agent_flow():
    with TestClient(app) as client:
        device = client.post(
            "/api/v1/devices/register",
            json={
                "device_code": "pytest-device",
                "name": "测试设备",
                "capabilities": ["accessibility"],
            },
        ).json()
        headers = {"X-Agent-Token": device["agent_token"]}
        task = client.post(
            "/api/v1/tasks",
            json={
                "name": "闭环测试任务",
                "publish_title": "测试标题",
                "body": "测试正文",
                "topics": ["测试"],
                "target_device_id": device["id"],
            },
        ).json()
        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]
        assert claimed["id"] == task["id"]

        updated = client.post(
            f"/api/v1/agent/tasks/{task['id']}/status",
            json={
                "device_id": device["id"],
                "lease_token": claimed["lease_token"],
                "status": "running",
                "step": "launching_douyin",
                "progress": 10,
                "message": "正在打开抖音",
            },
            headers=headers,
        )
        assert updated.status_code == 200

        detail = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert detail["status"] == "running"
        assert len(detail["logs"]) >= 3
        assert "token_hash" not in detail["device"]


def test_agent_authentication_and_lease_renewal():
    with TestClient(app) as client:
        device = client.post(
            "/api/v1/devices/register",
            json={
                "device_code": "pytest-auth-device",
                "name": "Authentication test device",
                "capabilities": ["task_pull", "lease_renewal"],
            },
        ).json()
        headers = {"X-Agent-Token": device["agent_token"]}
        client.post(
            "/api/v1/tasks",
            json={
                "name": "Lease renewal test task",
                "publish_title": "Lease renewal",
                "target_device_id": device["id"],
            },
        )

        rejected = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
        )
        assert rejected.status_code == 401

        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]
        renewed = client.post(
            f"/api/v1/agent/tasks/{claimed['id']}/lease",
            json={
                "device_id": device["id"],
                "lease_token": claimed["lease_token"],
            },
            headers=headers,
        )
        assert renewed.status_code == 200
        assert renewed.json()["lease_expires_at"]


def test_task_targets_specific_device():
    with TestClient(app) as client:
        device_a = _register_device(client, "target-dev-a", "账号A")
        device_b = _register_device(client, "target-dev-b", "账号B")
        headers_a = {"X-Agent-Token": device_a["agent_token"]}
        headers_b = {"X-Agent-Token": device_b["agent_token"]}

        task = client.post(
            "/api/v1/tasks",
            json={"name": "定向到账号B", "target_device_id": device_b["id"]},
        ).json()
        assert task["target_device_id"] == device_b["id"]

        # Device A must not be able to claim a task assigned to device B.
        claimed_a = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device_a["id"]},
            headers=headers_a,
        ).json()["task"]
        assert claimed_a is None

        claimed_b = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device_b["id"]},
            headers=headers_b,
        ).json()["task"]
        assert claimed_b is not None and claimed_b["id"] == task["id"]


def test_terminal_task_rejects_lease_renewal():
    with TestClient(app) as client:
        device = _register_device(client, "terminal-dev", "终态设备")
        headers = {"X-Agent-Token": device["agent_token"]}
        client.post(
            "/api/v1/tasks",
            json={"name": "取消传达测试", "target_device_id": device["id"]},
        )
        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]

        cancelled = client.post(
            f"/api/v1/tasks/{claimed['id']}/actions",
            json={"action": "cancel"},
        )
        assert cancelled.status_code == 200

        # The agent's next lease renewal must now be rejected so it stops.
        renewed = client.post(
            f"/api/v1/agent/tasks/{claimed['id']}/lease",
            json={"device_id": device["id"], "lease_token": claimed["lease_token"]},
            headers=headers,
        )
        assert renewed.status_code == 409


def test_auto_publish_pulls_and_consumes_content():
    with TestClient(app) as client:
        device = _register_device(client, "auto-pub-dev", "自动发布号")
        headers = {"X-Agent-Token": device["agent_token"]}
        toggled = client.patch(
            f"/api/v1/devices/{device['id']}/auto-publish",
            json={"auto_publish": True},
        )
        assert toggled.status_code == 200

        first = client.post(
            "/api/v1/content",
            json={"title": "标题一", "body": "正文一", "topics": ["话题"]},
        ).json()
        client.post("/api/v1/content", json={"body": "正文二"})

        # Claiming with auto-publish on pulls the oldest pooled content.
        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]
        assert claimed is not None
        assert claimed["content_id"] == first["id"]
        assert claimed["body"] == "正文一"

        pool = client.get("/api/v1/content").json()
        first_now = next(c for c in pool if c["id"] == first["id"])
        assert first_now["status"] == "publishing"

        for status, step in [("running", "launching_douyin"), ("succeeded", "completed")]:
            client.post(
                f"/api/v1/agent/tasks/{claimed['id']}/status",
                json={
                    "device_id": device["id"],
                    "lease_token": claimed["lease_token"],
                    "status": status,
                    "step": step,
                    "progress": 100 if status == "succeeded" else 10,
                    "message": status,
                },
                headers=headers,
            )

        pool = client.get("/api/v1/content").json()
        first_now = next(c for c in pool if c["id"] == first["id"])
        assert first_now["status"] == "published"
        assert first_now["published_device_id"] == device["id"]


def test_group_sync_and_broadcast():
    with TestClient(app) as client:
        device = _register_device(client, "grp-dev", "群发号")
        headers = {"X-Agent-Token": device["agent_token"]}
        synced = client.post(
            "/api/v1/agent/groups",
            json={
                "device_id": device["id"],
                "groups": [
                    {"name": "测试群A", "member_count": 7, "can_mention_all": True},
                    {"name": "测试群B"},
                ],
            },
            headers=headers,
        )
        assert synced.status_code == 200 and synced.json()["synced"] == 2

        groups = client.get(f"/api/v1/devices/{device['id']}/groups").json()
        assert len(groups) == 2
        admin = [g for g in groups if g["can_mention_all"]]
        assert admin and admin[0]["group_name"] == "测试群A"

        created = client.post(
            "/api/v1/broadcasts",
            json={
                "target_device_id": device["id"],
                "text": "群发测试消息",
                "mention_all": True,
                "groups": ["测试群A"],
            },
        ).json()
        assert created["publish_type"] == "group_message"
        assert created["mention_all"] is True
        assert created["target_groups"] == ["测试群A"]

        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]
        assert claimed["id"] == created["id"]
        assert claimed["publish_type"] == "group_message"


def test_abnormal_account_is_paused():
    with TestClient(app) as client:
        device = _register_device(client, "abn-dev", "异常号")
        headers = {"X-Agent-Token": device["agent_token"]}
        client.patch(
            f"/api/v1/devices/{device['id']}/auto-publish",
            json={"auto_publish": True},
        )
        client.post("/api/v1/content", json={"body": "异常号内容"})
        # mark the account abnormal
        flagged = client.patch(
            f"/api/v1/devices/{device['id']}/health",
            json={"health": "abnormal", "health_message": "账号被限流"},
        )
        assert flagged.status_code == 200

        # auto-publish must not generate a task for an abnormal account
        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]
        assert claimed is None

        # explicit scheduling is refused too
        moment = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        refused = client.post(
            f"/api/v1/devices/{device['id']}/schedule", json={"times": [moment]}
        )
        assert refused.status_code == 409


def test_retry_failed_task_requeues_content():
    with TestClient(app) as client:
        device = _register_device(client, "retry-dev", "重发号")
        client.post("/api/v1/content", json={"title": "重发", "body": "重发正文"})
        moment = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        client.post(
            f"/api/v1/devices/{device['id']}/schedule", json={"times": [moment]}
        )
        # whichever content the scheduler consumed for this device
        task = max(
            (
                t
                for t in client.get("/api/v1/tasks").json()
                if t.get("content_id") and t.get("target_device_id") == device["id"]
            ),
            key=lambda t: t["id"],
        )
        cid = task["content_id"]
        client.post(f"/api/v1/tasks/{task['id']}/actions", json={"action": "cancel"})

        new = client.post(
            f"/api/v1/tasks/{task['id']}/actions", json={"action": "retry"}
        ).json()
        assert new["id"] != task["id"]
        assert new["status"] == "queued"
        assert new["content_id"] == cid
        assert new["body"] == task["body"]  # serialization is complete, not empty

        item = next(
            c for c in client.get("/api/v1/content").json() if c["id"] == cid
        )
        assert item["status"] == "publishing"
        assert item["published_task_id"] == new["id"]


def test_content_csv_import():
    with TestClient(app) as client:
        csv = (
            "封面文字,正文,标题,话题,城市\n"
            "封面A,正文A,标题A,上海买房 沪漂,上海\n"
            ",正文B,,,,\n"  # only body
            ",,,,\n"  # blank -> skipped
        )
        res = client.post(
            "/api/v1/content/import",
            files={"file": ("c.csv", csv.encode("utf-8"), "text/csv")},
        )
        assert res.status_code == 200, res.text
        assert res.json()["created"] == 2
        pool = client.get("/api/v1/content").json()
        a = next(c for c in pool if c["body"] == "正文A")
        assert a["city"] == "上海"
        assert a["topics"] == ["上海买房", "沪漂"]
        # Clean up so leftover pending content doesn't pollute other tests
        # (the suite shares one DB and consumes the oldest pending item).
        for c in pool:
            if c["body"] in ("正文A", "正文B"):
                client.delete(f"/api/v1/content/{c['id']}")


def test_admin_auth_guards_management_api():
    from app.core.config import get_settings

    settings = get_settings()
    settings.admin_token = "test-admin"  # enable enforcement for this test
    try:
        with TestClient(app) as client:
            # No token -> 401 on management routes.
            assert client.get("/api/v1/devices").status_code == 401
            assert client.post("/api/v1/content", json={"body": "x"}).status_code == 401
            # Correct token (header) -> allowed.
            h = {"X-Admin-Token": "test-admin"}
            assert client.get("/api/v1/devices", headers=h).status_code == 200
            # Agent endpoints stay reachable without the admin token (device auth).
            reg = client.post(
                "/api/v1/devices/register",
                json={"device_code": "auth-dev", "name": "x", "capabilities": []},
            )
            assert reg.status_code == 200
            # Screenshot file accepts the token via query param (for <img>).
            assert client.get("/api/v1/agent/screenshots/999/file").status_code == 401
            assert (
                client.get("/api/v1/agent/screenshots/999/file?token=test-admin").status_code
                == 404  # authorized, just doesn't exist
            )
    finally:
        settings.admin_token = ""


def test_per_platform_schedule_and_matching():
    import datetime as _dt

    with TestClient(app) as client:
        device = _register_device(client, "platf-dev", "双平台机")
        # an xhs account on the same phone
        client.post(
            "/api/v1/agent/accounts",
            json={"device_id": device["id"], "accounts": [{"platform": "xhs"}]},
            headers={"X-Agent-Token": device["agent_token"]},
        )
        # content per platform
        client.post("/api/v1/content", json={"body": "抖音内容", "platform": "douyin"})
        xhs = client.post(
            "/api/v1/content", json={"body": "小红书内容", "platform": "xhs"}
        ).json()
        # schedule for the xhs account only
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        res = client.post(
            f"/api/v1/devices/{device['id']}/schedule",
            json={"times": [now], "platform": "xhs"},
        ).json()
        assert res["scheduled"] == 1
        # the created task is an xhs task consuming the xhs content
        tasks = [t for t in client.get("/api/v1/tasks").json() if t["platform"] == "xhs"]
        assert len(tasks) == 1
        assert tasks[0]["content_id"] == xhs["id"]
        assert tasks[0]["body"] == "小红书内容"


def test_metrics_回采_and_performance():
    with TestClient(app) as client:
        device = _register_device(client, "metric-dev", "回采机")
        headers = {"X-Agent-Token": device["agent_token"]}
        # publish a content (so a succeeded task exists to match against)
        content = client.post(
            "/api/v1/content", json={"cover_title": "上海买房避坑", "body": "正文内容"}
        ).json()
        # a succeeded douyin post for this content on this device
        from app.db.session import engine
        from app.models.entities import PublishTask

        with Session(engine) as s:
            s.add(
                PublishTask(
                    name="上海买房避坑",
                    platform="douyin",
                    publish_type="text",
                    cover_title="上海买房避坑",
                    content_id=content["id"],
                    device_id=device["id"],
                    status="succeeded",
                )
            )
            s.commit()
        # agent 回采 reports metrics for the note titled like the cover
        client.post(
            "/api/v1/agent/metrics",
            json={
                "device_id": device["id"],
                "platform": "douyin",
                "posts": [
                    {"title": "上海买房避坑", "views": 1000, "likes": 88, "comments": 5}
                ],
            },
            headers=headers,
        )
        perf = client.get("/api/v1/content/performance").json()
        mine = [r for r in perf if r["content_id"] == content["id"]]
        assert mine and mine[0]["likes"] == 88 and mine[0]["views"] == 1000


def test_device_accounts_model():
    with TestClient(app) as client:
        device = _register_device(client, "multi-acct", "多账号机")
        headers = {"X-Agent-Token": device["agent_token"]}
        # register created a douyin account row
        dev = next(
            d for d in client.get("/api/v1/devices").json() if d["id"] == device["id"]
        )
        assert any(a["platform"] == "douyin" for a in dev["accounts"])
        # agent reports an additional xhs account
        client.post(
            "/api/v1/agent/accounts",
            json={
                "device_id": device["id"],
                "accounts": [
                    {"platform": "xhs", "nickname": "小红薯", "account_id": "rb123"}
                ],
            },
            headers=headers,
        )
        dev = next(
            d for d in client.get("/api/v1/devices").json() if d["id"] == device["id"]
        )
        platforms = {a["platform"] for a in dev["accounts"]}
        assert platforms == {"douyin", "xhs"}
        xhs = next(a for a in dev["accounts"] if a["platform"] == "xhs")
        assert xhs["account_id"] == "rb123"


def test_delete_task_and_retention_purge():
    from app.db.session import engine
    from app.models.entities import PublishTask
    from app.services.tasks import purge_old_data

    with TestClient(app) as client:
        device = _register_device(client, "purge-dev", "清理设备")
        t1 = client.post(
            "/api/v1/tasks", json={"name": "删一个", "target_device_id": device["id"]}
        ).json()
        # delete endpoint removes it
        assert client.delete(f"/api/v1/tasks/{t1['id']}").status_code == 204
        assert client.get(f"/api/v1/tasks/{t1['id']}").status_code == 404

        # retention purge removes old finished tasks only
        t2 = client.post(
            "/api/v1/tasks", json={"name": "旧任务", "target_device_id": device["id"]}
        ).json()
        with Session(engine) as session:
            task = session.get(PublishTask, t2["id"])
            task.status = "succeeded"
            task.created_at = datetime.now(timezone.utc) - timedelta(days=40)
            session.add(task)
            session.commit()
            assert purge_old_data(session, days=30) >= 1
        assert client.get(f"/api/v1/tasks/{t2['id']}").status_code == 404


def test_provision_key_guards_registration():
    from app.core.config import get_settings

    settings = get_settings()
    settings.provision_key = "secret-key"
    try:
        with TestClient(app) as client:
            body = {"device_code": "pk-dev", "name": "x", "capabilities": []}
            # No key -> 401.
            assert client.post("/api/v1/devices/register", json=body).status_code == 401
            # Wrong key -> 401.
            assert client.post(
                "/api/v1/devices/register", json=body,
                headers={"X-Provision-Key": "nope"},
            ).status_code == 401
            # Correct key -> 200.
            assert client.post(
                "/api/v1/devices/register", json=body,
                headers={"X-Provision-Key": "secret-key"},
            ).status_code == 200
    finally:
        settings.provision_key = ""


def test_auto_publish_matches_content_city():
    with TestClient(app) as client:
        device = _register_device(client, "sh-dev", "上海号")
        headers = {"X-Agent-Token": device["agent_token"]}
        # ⚠ 先定城市、再开开关。城市策略上线之后，城市是主单位：一个号搬进一个
        # 「全城关着」的城市，它就跟着城市不发了（这是运营要的语义）。反过来
        # 在城市里点开这个号，会被翻译成「强制开」的账号例外，所以照样能发。
        # 这条用例测的是「按城市取内容」，开关只是前置条件。
        client.patch(f"/api/v1/devices/{device['id']}/profile", json={"city": "上海"})
        client.patch(
            f"/api/v1/devices/{device['id']}/auto-publish",
            json={"auto_publish": True},
        )

        # A 杭州-only item, a 上海 item, and a 通用 item.
        client.post("/api/v1/content", json={"body": "杭州内容", "city": "杭州"})
        sh = client.post("/api/v1/content", json={"body": "上海内容", "city": "上海"}).json()
        client.post("/api/v1/content", json={"body": "通用内容", "city": "通用"})

        # The 上海 account claims the 上海 item, never the 杭州 one.
        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]
        assert claimed["content_id"] == sh["id"]
        assert claimed["body"] == "上海内容"


def test_schedule_posts_across_days_enforces_per_day_quota():
    with TestClient(app) as client:
        device = _register_device(client, "sched-dev", "排期号")
        # daily quota of 2 posts per China day.
        client.patch(
            f"/api/v1/devices/{device['id']}/auto-publish",
            json={"auto_publish": True, "daily_quota": 2},
        )
        # 5 pooled content items available.
        for i in range(5):
            client.post("/api/v1/content", json={"body": f"正文{i}"})

        # Two slots tomorrow + two the day after = within quota (2/day);
        # a 3rd slot tomorrow must be rejected by the per-day quota.
        base = datetime.now(timezone.utc).replace(microsecond=0)
        d1 = base + timedelta(days=1)
        d2 = base + timedelta(days=2)
        times = [
            d1.replace(hour=1, minute=0).isoformat(),   # day1 #1
            d1.replace(hour=3, minute=0).isoformat(),   # day1 #2
            d1.replace(hour=5, minute=0).isoformat(),   # day1 #3 -> over quota
            d2.replace(hour=1, minute=0).isoformat(),   # day2 #1
            d2.replace(hour=3, minute=0).isoformat(),   # day2 #2
        ]
        res = client.post(
            f"/api/v1/devices/{device['id']}/schedule",
            json={"times": times},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["scheduled"] == 4          # 2 per day x 2 days
        assert body["skipped_quota"] == 1      # the 3rd slot on day1
        assert body["daily_quota"] == 2

        # 4 scheduled auto-publish tasks now exist for this device.
        tasks = client.get("/api/v1/tasks").json()
        mine = [t for t in tasks if t.get("target_device_id") == device["id"]]
        assert len(mine) == 4
        assert all(t["scheduled_at"] for t in mine)


def test_expired_lease_is_reclaimed_and_device_freed():
    from app.db.session import engine
    from app.models.entities import Device, PublishTask
    from app.services.tasks import reclaim_expired_tasks

    with TestClient(app) as client:
        device = _register_device(client, "reclaim-dev", "回收设备")
        headers = {"X-Agent-Token": device["agent_token"]}
        client.post(
            "/api/v1/tasks",
            json={"name": "租约过期回收", "target_device_id": device["id"]},
        )
        claimed = client.post(
            "/api/v1/agent/tasks/claim",
            json={"device_id": device["id"]},
            headers=headers,
        ).json()["task"]

        # Backdate the lease so it looks abandoned.
        with Session(engine) as session:
            task = session.get(PublishTask, claimed["id"])
            task.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            session.add(task)
            session.commit()
            assert reclaim_expired_tasks(session) >= 1

        detail = client.get(f"/api/v1/tasks/{claimed['id']}").json()
        assert detail["status"] == "failed"

        with Session(engine) as session:
            freed = session.get(Device, device["id"])
            assert freed.current_task_id is None
            assert freed.status == "online"
