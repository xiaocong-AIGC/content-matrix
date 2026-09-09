"""Console token lifecycle: generate named tokens with validity, freeze/extend,
operator self-service renewal, role enforcement, and revoke."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _create(client, master, name, role="operator", valid_days=30):
    r = client.post(
        "/api/v1/auth/tokens",
        headers=master,
        json={"name": name, "role": role, "valid_days": valid_days},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_token_lifecycle_roles_and_renewal():
    settings = get_settings()
    settings.admin_token = "master-test"
    master = {"X-Admin-Token": "master-test"}
    try:
        with TestClient(app) as client:
            me = client.get("/api/v1/auth/me", headers=master).json()
            assert me["name"] == "主令牌" and me["role"] == "admin" and me["auth"] is True

            created = _create(client, master, "运营A", valid_days=30)
            raw, tid = created["token"], created["id"]
            assert created["role"] == "operator"
            assert created["expires_at"] and 28 <= created["days_left"] <= 30
            op = {"X-Admin-Token": raw}

            # operator works on the console, sees own status, can't manage tokens
            assert client.get("/api/v1/devices", headers=op).status_code == 200
            assert client.get("/api/v1/auth/me", headers=op).json()["name"] == "运营A"
            assert client.get("/api/v1/auth/tokens", headers=op).status_code == 403

            # FREEZE → operator blocked on console (403) but /me still readable
            client.patch(f"/api/v1/auth/tokens/{tid}/freeze", headers=master, json={"frozen": True})
            assert client.get("/api/v1/devices", headers=op).status_code == 403
            assert client.get("/api/v1/auth/me", headers=op).json()["status"] == "frozen"
            # UNFREEZE → works again
            client.patch(f"/api/v1/auth/tokens/{tid}/freeze", headers=master, json={"frozen": False})
            assert client.get("/api/v1/devices", headers=op).status_code == 200

            # renewal info reachable by the operator (note + qr availability)
            info = client.get("/api/v1/auth/renewal-info", headers=op).json()
            assert "note" in info and "wechat" in info and "alipay" in info

            # operator submits a 续费申请 WITH payment evidence (just a request)
            sub = client.post(
                "/api/v1/auth/request-renewal",
                headers=op,
                data={"amount": "99", "paid_at_text": "今天10:00", "reference": "wx-7788"},
            )
            assert sub.status_code == 200 and sub.json()["status"] == "pending"

            # token shows 待续费; admin sees the pending request with the evidence
            row = next(t for t in client.get("/api/v1/auth/tokens", headers=master).json() if t["id"] == tid)
            assert row["renewal_requested"] is True
            reqs = client.get("/api/v1/auth/renewal-requests", headers=master).json()
            req = next(x for x in reqs if x["token_id"] == tid)
            assert req["amount"] == "99" and req["reference"] == "wx-7788" and req["status"] == "pending"

            # nothing auto-extended yet — only on admin approval
            mine = client.get("/api/v1/auth/my-renewal", headers=op).json()
            assert mine["status"] == "pending"

            # admin verifies payment → approves 60 days → token extended, badge cleared
            appr = client.post(
                f"/api/v1/auth/renewal-requests/{req['id']}/approve",
                headers=master,
                json={"days": 60},
            ).json()
            assert appr["status"] == "approved" and appr["days_granted"] == 60
            row2 = next(t for t in client.get("/api/v1/auth/tokens", headers=master).json() if t["id"] == tid)
            assert row2["renewal_requested"] is False and row2["days_left"] >= 88
            assert client.get("/api/v1/auth/my-renewal", headers=op).json()["status"] == "approved"

            # a second request can be rejected with a reason
            client.post("/api/v1/auth/request-renewal", headers=op, data={"amount": "1"})
            req2 = client.get("/api/v1/auth/renewal-requests", headers=master).json()[0]
            rej = client.post(
                f"/api/v1/auth/renewal-requests/{req2['id']}/reject",
                headers=master,
                json={"note": "未收到款"},
            ).json()
            assert rej["status"] == "rejected" and rej["review_note"] == "未收到款"

            # DELETE → token stops working
            assert client.delete(f"/api/v1/auth/tokens/{tid}", headers=master).status_code == 204
            assert client.get("/api/v1/devices", headers=op).status_code == 401
            assert client.get("/api/v1/devices", headers={"X-Admin-Token": "nope"}).status_code == 401
    finally:
        settings.admin_token = ""
