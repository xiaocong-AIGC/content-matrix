import json
import os
import time
import urllib.error
import urllib.request

API_URL = os.getenv("SIMULATOR_API_URL", "http://127.0.0.1:8010/api/v1")
DEVICE_CODE = os.getenv("SIMULATOR_DEVICE_CODE", "simulator-01")
AGENT_TOKEN = ""

STEPS = [
    ("running", "launching_douyin", 10, "已启动抖音"),
    ("running", "opening_publish_entry", 22, "已进入发布入口"),
    ("running", "selecting_publish_type", 34, "已选择图文发布"),
    ("running", "editing_cover", 48, "已填写封面标题"),
    ("running", "filling_title", 60, "已填写发布标题"),
    ("running", "filling_body", 72, "已填写正文内容"),
    ("running", "adding_topics", 82, "已添加话题标签"),
    ("running", "reviewing", 92, "已进入发布确认页"),
]


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if AGENT_TOKEN:
        headers["X-Agent-Token"] = AGENT_TOKEN
    req = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def register() -> dict:
    return request(
        "POST",
        "/devices/register",
        {
            "device_code": DEVICE_CODE,
            "name": "Windows 模拟手机",
            "platform": "simulator",
            "agent_version": "0.1.0",
            "android_version": "simulated",
            "douyin_version": "simulated",
            "capabilities": ["task_pull", "status", "logs"],
        },
    )


def execute(device: dict, task: dict) -> None:
    task_id = task["id"]
    lease_token = task["lease_token"]
    for status, step, progress, message in STEPS:
        request(
            "POST",
            f"/agent/tasks/{task_id}/status",
            {
                "device_id": device["id"],
                "lease_token": lease_token,
                "status": status,
                "step": step,
                "progress": progress,
                "message": message,
            },
        )
        time.sleep(0.8)

    final_status = (
        "waiting_confirmation"
        if task["publish_mode"] == "manual_confirm"
        else "succeeded"
    )
    final_step = (
        "waiting_confirmation"
        if final_status == "waiting_confirmation"
        else "completed"
    )
    request(
        "POST",
        f"/agent/tasks/{task_id}/status",
        {
            "device_id": device["id"],
            "lease_token": lease_token,
            "status": final_status,
            "step": final_step,
            "progress": 96 if final_status == "waiting_confirmation" else 100,
            "message": (
                "内容已准备完毕，等待人工点击发布"
                if final_status == "waiting_confirmation"
                else "模拟发布完成"
            ),
        },
    )


def main() -> None:
    global AGENT_TOKEN
    device = register()
    AGENT_TOKEN = device["agent_token"]
    print(f"设备已注册: {device['name']} #{device['id']}")
    while True:
        try:
            request(
                "POST",
                f"/devices/{device['id']}/heartbeat",
                {"status": "online", "current_task_id": None},
            )
            task = request(
                "POST",
                "/agent/tasks/claim",
                {"device_id": device["id"]},
            )["task"]
            if task:
                print(f"领取任务 #{task['id']}: {task['name']}")
                execute(device, task)
            else:
                time.sleep(2)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"连接后端失败: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
