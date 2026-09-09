import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.entities import Device, DeviceAccount, ImageAsset, PublishTask
from app.schemas.dto import BroadcastCreate
from app.api.v1.routes.tasks import serialize_task

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])


@router.post("", status_code=201)
def create_broadcast(payload: BroadcastCreate, session: Session = Depends(get_session)):
    device = session.get(Device, payload.target_device_id)
    if not device:
        raise HTTPException(status_code=404, detail="指定的执行设备不存在")
    # ⚠ 判的必须是**账号**的状态，不是设备的。这两张表的 health 是分开维护的：
    # PATCH /devices/{id}/health 只写 DeviceAccount，所以之前判 Device.health 会出现
    # 「账号被标记异常、群发照发」。发布那条路（schedule_posts / maybe_generate_auto_task）
    # 一直判的是 DeviceAccount.health，这里跟它对齐。
    account = session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.device_id == device.id)
        .where(DeviceAccount.platform == "douyin")
    ).first()
    unhealthy = (account.health if account else device.health) or "normal"
    if unhealthy != "normal":
        raise HTTPException(
            status_code=409,
            detail=f"这个号被标记为「{unhealthy}」，先处理完再发",
        )
    image_path: str | None = None
    if payload.image_id is not None:
        asset = session.get(ImageAsset, payload.image_id)
        if not asset:
            raise HTTPException(status_code=404, detail="图片不存在")
        image_path = asset.path
    task = PublishTask(
        name=f"群发：{payload.text[:16]}",
        publish_type="group_message",
        body=payload.text,
        publish_mode="auto_publish",
        target_device_id=payload.target_device_id,
        image_path=image_path,
        mention_all=payload.mention_all,
        target_groups_json=json.dumps(payload.groups, ensure_ascii=False),
        scheduled_at=payload.scheduled_at,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return serialize_task(task)
