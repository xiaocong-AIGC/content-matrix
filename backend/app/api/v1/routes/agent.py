from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.session import get_session
from app.models.entities import ChatGroup, Device, PublishTask, Screenshot
from app.schemas.dto import (
    AccountReport,
    AgentLogCreate,
    AgentStatusUpdate,
    ClaimRequest,
    GroupSync,
    LeaseRenewRequest,
    MetricReport,
)
from app.core.admin_auth import require_admin
from app.services.device_auth import authenticate_device
from app.services.tasks import (
    append_agent_log,
    claim_next_task,
    renew_lease,
    update_agent_status,
    validate_lease,
)

router = APIRouter(prefix="/agent", tags=["agent"])

# Max screenshots retained per task (older ones are pruned on upload).
SCREENSHOTS_PER_TASK = 12


@router.post("/metrics")
def report_metrics(
    payload: MetricReport,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    """Agent reports 回采ed public metrics for an account's recent posts."""
    from app.services.metrics import record_metrics

    device = authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    stored = record_metrics(session, device.id, payload.platform, payload.posts)
    return {"stored": stored}


@router.post("/accounts")
def report_accounts(
    payload: AccountReport,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    """Agent reports the platform accounts logged in on this phone (douyin + xhs).
    Upserts each; operator-set knobs (city/quota/health) are preserved."""
    from app.services.accounts import serialize_account, upsert_account

    device = authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    out = []
    for acc in payload.accounts:
        out.append(
            serialize_account(
                upsert_account(
                    session, device.id, acc.platform,
                    nickname=acc.nickname, account_id=acc.account_id,
                    logged_in=acc.logged_in,
                )
            )
        )
    return {"accounts": out}


@router.get("/selectors")
def get_selectors():
    """Serve Douyin UI selector overrides so a redesign can be patched without a
    new APK. Ops drop a `selectors.json` in the storage dir; missing keys fall
    back to the agent's built-in defaults. Empty object = use all defaults."""
    import json

    path = get_settings().storage_dir / "selectors.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


@router.post("/tasks/claim")
def claim(
    payload: ClaimRequest,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    task = claim_next_task(session, payload.device_id)
    return {"task": task.model_dump() if task else None}


@router.post("/tasks/{task_id}/status")
def status(
    task_id: int,
    payload: AgentStatusUpdate,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return update_agent_status(session, task, payload)


@router.post("/tasks/{task_id}/logs", status_code=201)
def logs(
    task_id: int,
    payload: AgentLogCreate,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return append_agent_log(session, task, payload)


@router.post("/tasks/{task_id}/lease")
def lease(
    task_id: int,
    payload: LeaseRenewRequest,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    renewed = renew_lease(session, task, payload.device_id, payload.lease_token)
    return {"lease_expires_at": renewed.lease_expires_at}


@router.post("/tasks/{task_id}/screenshots", status_code=201)
async def screenshot(
    task_id: int,
    device_id: int = Form(...),
    lease_token: str = Form(...),
    step: str = Form(...),
    width: int | None = Form(None),
    height: int | None = Form(None),
    file: UploadFile = File(...),
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    authenticate_device(session.get(Device, device_id), x_agent_token)
    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    validate_lease(task, device_id, lease_token)
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="截图过大")
    directory = settings.storage_dir / "screenshots" / str(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    raw_suffix = Path(file.filename or "screen.png").suffix.lower()
    suffix = raw_suffix if raw_suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    path = directory / f"{uuid4().hex}{suffix}"
    path.write_bytes(data)
    record = Screenshot(
        task_id=task_id,
        device_id=device_id,
        step=step,
        file_path=str(path),
        content_type=file.content_type or "image/png",
        width=width,
        height=height,
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    # Retention: keep only the most recent screenshots per task so the storage
    # directory does not grow without bound.
    stale = session.exec(
        select(Screenshot)
        .where(Screenshot.task_id == task_id)
        .order_by(Screenshot.created_at.desc())
        .offset(SCREENSHOTS_PER_TASK)
    ).all()
    for item in stale:
        try:
            Path(item.file_path).unlink(missing_ok=True)
        except OSError:
            pass
        session.delete(item)
    if stale:
        session.commit()
    return record


@router.post("/groups")
def sync_groups(
    payload: GroupSync,
    x_agent_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    """Agent 扫到的群列表。**全量替换，但不接受"整张表换成一批陌生名字"。**

    ⚠ 2026-09-08 的事故：一次扫描在两台设备上都读成了一条私聊的名字
    （`member_count` 为空 —— 真群有人数），把正确的群名整个覆盖掉。
    当天晚班三条群发全部「未找到群」，而运营看到的是「群明明在，怎么会找不到」。
    正确的群名是我翻**历史成功任务**的 `target_groups` 才捞回来的 —— 差一点就永久丢了。

    这里加一道**只拦最坏情况**的闸：新旧两批群名**完全不相交**时不覆盖，
    保留旧表并报一条告警。理由：
    - 群改名、加群、退群都会让两批**部分**不同 —— 那是正常的，照常覆盖；
    - **完全不相交**只有两种可能：换号了（那会走「更新账号」，账号变更本身有记录），
      或者这次扫描读错了页面。后者的代价是自动推送从此天天推空，
      而且失败信息是「未找到群」，指不到真正的原因。
    - 拦下来比误覆盖便宜：真换号了，运营再点一次「更新账号」就过了。
    """
    authenticate_device(session.get(Device, payload.device_id), x_agent_token)
    old_rows = session.exec(
        select(ChatGroup).where(ChatGroup.device_id == payload.device_id)
    ).all()
    old_names = {g.group_name for g in old_rows if g.group_name}
    new_names = {info.name for info in payload.groups if info.name}

    if old_names and new_names and not (old_names & new_names):
        # 一个都对不上 —— 大概率是扫错了页面。保留旧表，把这件事说出来。
        try:
            from app.services import notify

            notify.enqueue(
                event="group_sync_rejected",
                dedup_key=f"groupsync:{payload.device_id}:{sorted(new_names)[0][:20]}",
                title="群列表扫描结果被拦下",
                severity="warning",
                payload={"lines": [
                    f"设备 #{payload.device_id} 这次扫到的群和原来的**一个都对不上**，已保留原列表。",
                    f"原来：{'、'.join(sorted(old_names))}",
                    f"扫到：{'、'.join(sorted(new_names))}",
                    "如果这台确实换号了，去设备上点一次「更新账号」即可覆盖。",
                ]},
            )
        except Exception:  # noqa: BLE001 — 通知失败不能影响同步本身
            pass
        return {"synced": 0, "rejected": True, "kept": sorted(old_names)}

    for existing in old_rows:
        session.delete(existing)
    for info in payload.groups:
        session.add(
            ChatGroup(
                device_id=payload.device_id,
                group_name=info.name,
                member_count=info.member_count,
                can_mention_all=info.can_mention_all,
            )
        )
    session.commit()
    return {"synced": len(payload.groups)}


@router.get("/tasks/{task_id}/image")
def task_image(task_id: int, session: Session = Depends(get_session)):
    task = session.get(PublishTask, task_id)
    if not task or not task.image_path:
        raise HTTPException(status_code=404, detail="任务无图片")
    path = Path(task.image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return FileResponse(path)


@router.get("/screenshots/{screenshot_id}/file", dependencies=[Depends(require_admin)])
def screenshot_file(
    screenshot_id: int,
    session: Session = Depends(get_session),
):
    record = session.get(Screenshot, screenshot_id)
    if not record:
        raise HTTPException(status_code=404, detail="截图不存在")
    path = Path(record.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="截图文件不存在")
    return FileResponse(path, media_type=record.content_type)


# ── 云机 adb 地址配置 (first-run wizard 填, orchestrator 读) ──────────────────
from app.services import settings_store  # noqa: E402
from app.core.admin_auth import require_admin as _require_admin  # noqa: E402
from app.schemas.dto import AdbEndpointsUpdate  # noqa: E402

_ADB_KEY = "adb_endpoints"
_DEFAULT_ADB = "192.168.1.200:6001-6037"


@router.get("/adb-endpoints")
def get_adb_endpoints(session: Session = Depends(get_session)):
    """The orchestrator polls this to know which cloud-phone endpoints to connect.
    Empty stored value → the built-in default (the owner's box)."""
    return {"endpoints": settings_store.get(session, _ADB_KEY) or _DEFAULT_ADB}


@router.put("/adb-endpoints", dependencies=[Depends(_require_admin)])
def put_adb_endpoints(
    payload: AdbEndpointsUpdate, session: Session = Depends(get_session)
):
    settings_store.put(session, _ADB_KEY, payload.endpoints.strip())
    return {"endpoints": payload.endpoints.strip()}
