import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.entities import (
    Device,
    DeviceAccount,
    ExecutionLog,
    PublishTask,
    Screenshot,
)
from app.core.enums import TERMINAL_STATUSES, DeviceStatus, TaskStatus
from app.core.serialization import normalize_datetimes
from app.models.entities import utcnow
from app.schemas.dto import TaskAction, TaskCreate
from app.services.tasks import (
    _finalize_content,
    append_log,
    create_task,
    retry_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def serialize_task(
    task: PublishTask,
    device: Device | None = None,
    account_nickname: str | None = None,
) -> dict:
    data = task.model_dump()
    data["topics"] = json.loads(task.topics_json)
    data["media"] = json.loads(task.media_json)
    if device:
        device_data = device.model_dump()
        device_data.pop("token_hash", None)
        data["device"] = device_data
    else:
        data["device"] = None
    # 平台对应的账号昵称（抖音任务→抖音号昵称，小红书任务→小红书昵称），
    # 让执行记录能同时显示「设备 + 发布账号」，而不是一律显示抖音昵称。
    data["account_nickname"] = account_nickname
    data["target_groups"] = json.loads(task.target_groups_json or "[]")
    data.pop("topics_json", None)
    data.pop("media_json", None)
    data.pop("target_groups_json", None)
    data.pop("lease_token", None)
    return normalize_datetimes(data)


@router.post("", status_code=201)
def create(payload: TaskCreate, session: Session = Depends(get_session)):
    return serialize_task(create_task(session, payload))


@router.get("")
def list_tasks(
    limit: int = 300,
    session: Session = Depends(get_session),
):
    """Most recent tasks (bounded). The console polls this every 2s, so an
    unbounded list would make payloads grow without limit as history piles up.
    Older tasks remain in /tasks/{id} and the paginated history view; default
    300 covers all active work + recent history comfortably."""
    limit = max(1, min(limit, 2000))
    tasks = session.exec(
        select(PublishTask).order_by(PublishTask.created_at.desc()).limit(limit)
    ).all()
    devices = {item.id: item for item in session.exec(select(Device)).all()}
    accounts = {
        (a.device_id, a.platform): a.nickname
        for a in session.exec(select(DeviceAccount)).all()
    }
    return [
        serialize_task(
            task,
            devices.get(task.device_id),
            accounts.get((task.device_id, task.platform)),
        )
        for task in tasks
    ]


@router.get("/{task_id}")
def get_task(task_id: int, session: Session = Depends(get_session)):
    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    device = session.get(Device, task.device_id) if task.device_id else None
    logs = session.exec(
        select(ExecutionLog)
        .where(ExecutionLog.task_id == task_id)
        .order_by(ExecutionLog.created_at)
    ).all()
    screenshots = session.exec(
        select(Screenshot)
        .where(Screenshot.task_id == task_id)
        .order_by(Screenshot.created_at.desc())
    ).all()
    result = serialize_task(task, device)
    result["logs"] = [
        {
            **log.model_dump(),
            "context": json.loads(log.context_json),
        }
        for log in logs
    ]
    for log in result["logs"]:
        log.pop("context_json", None)
    result["screenshots"] = [
        {key: value for key, value in item.model_dump().items() if key != "file_path"}
        for item in screenshots
    ]
    return normalize_datetimes(result)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, session: Session = Depends(get_session)):
    """Permanently remove a task and its logs/screenshots (+ screenshot files)."""
    import os

    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    for shot in session.exec(
        select(Screenshot).where(Screenshot.task_id == task_id)
    ).all():
        try:
            os.remove(shot.file_path)
        except OSError:
            pass
        session.delete(shot)
    for log in session.exec(
        select(ExecutionLog).where(ExecutionLog.task_id == task_id)
    ).all():
        session.delete(log)
    if task.device_id:
        device = session.get(Device, task.device_id)
        if device and device.current_task_id == task_id:
            device.current_task_id = None
            device.status = DeviceStatus.ONLINE
            session.add(device)
    session.delete(task)
    session.commit()


@router.post("/{task_id}/actions")
def task_action(
    task_id: int,
    payload: TaskAction,
    session: Session = Depends(get_session),
):
    task = session.get(PublishTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if payload.action == "retry":
        return serialize_task(retry_task(session, task))
    current = TaskStatus(task.status)

    if payload.action == "cancel":
        # Cancel is allowed from ANY live or failed state (dismiss) — only a task
        # that's already succeeded/cancelled has nothing to cancel. Cancelling
        # runs _finalize_content below → the content returns to the pool so the
        # 内容库「发布中」计数 drops.
        if current in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            raise HTTPException(status_code=409, detail="该任务已结束，无需取消")
        task.status = TaskStatus.CANCELLED
        task.current_step = "cancelled"
        task.finished_at = utcnow()
        message = payload.message or "操作员取消任务"
    elif current in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="终态任务不能再次操作")
    elif payload.action == "confirm_published":
        if current != TaskStatus.WAITING_CONFIRMATION:
            raise HTTPException(status_code=409, detail="任务当前不在等待确认状态")
        task.status = TaskStatus.SUCCEEDED
        task.current_step = "completed"
        task.progress = 100
        task.finished_at = utcnow()
        message = payload.message or "操作员确认内容已在手机端发布"
    else:
        raise HTTPException(status_code=400, detail="不支持的任务动作")

    task.updated_at = utcnow()
    task.lease_expires_at = None
    if task.device_id:
        device = session.get(Device, task.device_id)
        if device:
            device.status = DeviceStatus.ONLINE
            device.current_task_id = None
            device.updated_at = utcnow()
            session.add(device)
    session.add(task)
    session.commit()
    session.refresh(task)
    _finalize_content(session, task, succeeded=task.status == TaskStatus.SUCCEEDED)
    append_log(
        session,
        task.id,
        task.device_id,
        "info",
        task.current_step,
        message,
    )
    return serialize_task(task)
