"""路由顺序：静态路径不能被同级的 `{param}` 吃掉。

这条测试是被一个上了线的 bug 逼出来的：`PUT /broadcast-library/{message_id}`
声明在 `/schedule` 前面，于是「保存自动推送设置」被当成「更新 id 为 'schedule'
的那条消息」，返回 422 —— **这个接口从写出来到上线一次都没成功过**，
而报的错是「message_id 不是整数」，和运营刚做的动作完全对不上。

FastAPI 按声明顺序匹配，这类错误不会在启动时报出来，只能靠这条测试守住。
"""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _shadows(pattern: str, concrete: str) -> bool:
    p, c = pattern.split("/"), concrete.split("/")
    if len(p) != len(c):
        return False
    return all(a.startswith("{") or a == b for a, b in zip(p, c))


def test_没有静态路径被同级的参数路径吃掉():
    routes = [
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or [])
    ]
    shadowed = [
        f"{m} {path} 吃掉了后面声明的 {m2} {other}"
        for i, (m, path) in enumerate(routes)
        if "{" in path
        for m2, other in routes[i + 1:]
        if m2 == m and "{" not in other and _shadows(path, other)
    ]
    assert not shadowed, "\n".join(shadowed)


def test_保存自动推送设置真的能存下去():
    """光有上面那条静态检查不够 —— 再走一遍真实请求。"""
    settings = get_settings()
    settings.admin_token = "route-order-test"
    headers = {"X-Admin-Token": "route-order-test"}
    try:
        with TestClient(app) as client:
            before = client.get(
                "/api/v1/broadcast-library/schedule", headers=headers
            ).json()
            r = client.put(
                "/api/v1/broadcast-library/schedule",
                headers=headers,
                json={"daily_count": 3, "windows": "09-10,20-21", "mode": "rotate"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["daily_count"] == 3
            assert r.json()["windows_readable"] == "09:00-10:00、20:00-21:00"
            assert r.json()["mode"] == "rotate"

            # 复原，别把测试的设置留在库里
            client.put(
                "/api/v1/broadcast-library/schedule",
                headers=headers,
                json={
                    "daily_count": before["daily_count"],
                    "windows": before["windows"],
                    "mode": before["mode"],
                },
            )
    finally:
        settings.admin_token = None
