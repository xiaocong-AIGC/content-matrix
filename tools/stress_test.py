#!/usr/bin/env python3
"""
Concurrency stress test — simulate many phones hammering the backend at once to
expose SQLite write contention ("database is locked"), duplicate task claims, or
lease bugs BEFORE committing to (or skipping) the Postgres migration.

Each simulated device runs a tight loop for the duration:
    heartbeat  ->  claim  ->  (renew lease + status update + screenshot upload)

It counts every request, flags 5xx / "locked" errors, checks that no task is ever
claimed by two different devices, and reports throughput + latency percentiles.

Usage:
    python stress_test.py --server http://127.0.0.1:8010/api/v1 \
        --token douyin-admin-2026 --devices 20 --seconds 30
"""
import argparse
import base64
import json
import threading
import time
import urllib.request
from collections import defaultdict

# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

lock = threading.Lock()
stats = defaultdict(int)
latencies = []
errors = []
claim_owner = {}  # task_id -> device_id (to detect double-claims)
double_claims = []


def req(method, url, token=None, body=None, json_body=None, multipart=None):
    headers = {}
    data = None
    if token:
        headers["X-Agent-Token"] = token
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif multipart is not None:
        boundary = "----stress" + str(time.time_ns())
        buf = b""
        for k, v in multipart["fields"].items():
            buf += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        fn = multipart["file"]
        buf += f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"s.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
        buf += fn + f"\r\n--{boundary}--\r\n".encode()
        data = buf
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = body
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        with lock:
            stats["exceptions"] += 1
            errors.append(str(e)[:120])
        return None, 0
    dt = (time.time() - t0) * 1000
    with lock:
        stats["requests"] += 1
        latencies.append(dt)
        if code >= 500 or "locked" in txt.lower():
            stats["server_errors"] += 1
            errors.append(f"{code}: {txt[:120]}")
    return txt, code


def worker(base, admin_token, dev, deadline):
    dev_id, token, task_id = dev["id"], dev["token"], dev["task_id"]
    lease = None
    while time.time() < deadline:
        req("POST", f"{base}/devices/{dev_id}/heartbeat", token=token,
            json_body={"status": "busy", "current_task_id": task_id, "agent_version": "stress"})
        txt, code = req("POST", f"{base}/agent/tasks/claim", token=token,
                        json_body={"device_id": dev_id})
        if txt:
            try:
                task = json.loads(txt).get("task")
            except ValueError:
                task = None
            if task:
                with lock:
                    prev = claim_owner.get(task["id"])
                    if prev is not None and prev != dev_id:
                        double_claims.append((task["id"], prev, dev_id))
                    claim_owner[task["id"]] = dev_id
                lease = task.get("lease_token")
        if lease:
            req("POST", f"{base}/agent/tasks/{task_id}/lease", token=token,
                json_body={"device_id": dev_id, "lease_token": lease})
            req("POST", f"{base}/agent/tasks/{task_id}/status", token=token,
                json_body={"device_id": dev_id, "lease_token": lease,
                           "status": "running", "step": "stress", "progress": 50,
                           "message": "stress"})
            req("POST", f"{base}/agent/tasks/{task_id}/screenshots", token=token,
                multipart={"fields": {"device_id": str(dev_id), "lease_token": lease,
                                      "step": "stress", "width": "1", "height": "1"},
                           "file": PNG})
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8010/api/v1")
    ap.add_argument("--token", default="douyin-admin-2026")
    ap.add_argument("--devices", type=int, default=20)
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()
    base, admin = args.server, args.token
    adm = {"X-Admin-Token": admin}

    print(f"[stress] registering {args.devices} devices…", flush=True)
    devs = []
    for i in range(args.devices):
        txt, _ = req("POST", f"{base}/devices/register",
                     json_body={"device_code": f"stress-{i}", "name": f"stress-{i}",
                                "capabilities": ["task_pull"]})
        d = json.loads(txt)
        # one task per device (admin-authenticated create)
        req2 = urllib.request.Request(f"{base}/tasks",
                                      data=json.dumps({"name": f"stress-task-{i}",
                                                       "target_device_id": d["id"], "body": "stress",
                                                       "publish_mode": "manual_confirm"}).encode(),
                                      headers={**adm, "Content-Type": "application/json"}, method="POST")
        task = json.loads(urllib.request.urlopen(req2).read())
        devs.append({"id": d["id"], "token": d["agent_token"], "task_id": task["id"]})

    print(f"[stress] running {args.seconds}s with {args.devices} concurrent devices…", flush=True)
    deadline = time.time() + args.seconds
    threads = [threading.Thread(target=worker, args=(base, admin, dv, deadline), daemon=True)
               for dv in devs]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    # Clean up the synthetic tasks/devices so they don't pollute 执行记录.
    print("[stress] cleaning up synthetic data…", flush=True)
    for dv in devs:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{base}/tasks/{dv['task_id']}", headers=adm, method="DELETE"))
        except Exception:  # noqa: BLE001
            pass
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{base}/devices/{dv['id']}", headers=adm, method="DELETE"))
        except Exception:  # noqa: BLE001
            pass

    latencies.sort()
    n = len(latencies) or 1
    p50 = latencies[int(n * 0.5)] if latencies else 0
    p95 = latencies[int(n * 0.95)] if latencies else 0
    p99 = latencies[min(int(n * 0.99), n - 1)] if latencies else 0
    print("\n========= RESULT =========")
    print(f"devices            : {args.devices}")
    print(f"duration           : {elapsed:.1f}s")
    print(f"total requests     : {stats['requests']}")
    print(f"throughput         : {stats['requests']/elapsed:.0f} req/s")
    print(f"server errors(5xx/locked): {stats['server_errors']}")
    print(f"client exceptions  : {stats['exceptions']}")
    print(f"double claims      : {len(double_claims)}")
    print(f"latency p50/p95/p99: {p50:.0f} / {p95:.0f} / {p99:.0f} ms")
    if errors:
        print("\nsample errors:")
        for e in errors[:5]:
            print("  ", e)
    print("==========================")
    ok = stats["server_errors"] == 0 and len(double_claims) == 0 and stats["exceptions"] == 0
    print("VERDICT:", "PASS" if ok else "ISSUES FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
