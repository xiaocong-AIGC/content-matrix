from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.serialization import normalize_datetimes
from app.db.session import get_session
from app.models.entities import ImageAsset, UploadAudit

router = APIRouter(prefix="/images", tags=["images"])


class ImageUpdate(BaseModel):
    title: str | None = None
    category: str | None = None


def serialize_image(asset: ImageAsset) -> dict:
    return normalize_datetimes(
        {
            "id": asset.id,
            "filename": asset.filename,
            "title": asset.title or Path(asset.filename).stem,
            "category": asset.category or "未分类",
            "url": f"/api/v1/images/{asset.id}/file",
            "created_at": asset.created_at,
        }
    )


@router.post("", status_code=201)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form("未分类"),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    # Reject non-images and oversized files (bounds disk-fill DoS).
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="仅支持图片文件")
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"图片过大（上限 {settings.max_upload_bytes // 1024 // 1024} MB）",
        )
    directory = settings.storage_dir / "images"
    directory.mkdir(parents=True, exist_ok=True)
    # Allow only known image extensions on the stored name (the rest is a uuid).
    raw_suffix = Path(file.filename or "image.jpg").suffix.lower()
    suffix = raw_suffix if raw_suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
    path = directory / f"{uuid4().hex}{suffix}"
    path.write_bytes(data)
    asset = ImageAsset(
        filename=file.filename or "image",
        title=title.strip() or Path(file.filename or "image").stem,
        category=category.strip() or "未分类",
        path=str(path),
        content_type=file.content_type or "image/jpeg",
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    # Audit trail (traceability): who uploaded what, from where, when.
    session.add(
        UploadAudit(
            image_id=asset.id,
            filename=asset.filename,
            category=asset.category,
            size_bytes=len(data),
            content_type=asset.content_type,
            client_ip=(request.client.host if request.client else ""),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    session.commit()
    return serialize_image(asset)


@router.get("/audit")
def upload_audit(session: Session = Depends(get_session)):
    rows = session.exec(
        select(UploadAudit).order_by(UploadAudit.created_at.desc()).limit(200)
    ).all()
    return [normalize_datetimes(r.model_dump()) for r in rows]


@router.get("")
def list_images(
    category: str | None = None, session: Session = Depends(get_session)
):
    statement = select(ImageAsset).order_by(ImageAsset.created_at.desc())
    if category:
        statement = statement.where(ImageAsset.category == category)
    assets = session.exec(statement).all()
    return [serialize_image(a) for a in assets]


@router.get("/categories")
def list_categories(session: Session = Depends(get_session)):
    rows = session.exec(select(ImageAsset.category)).all()
    seen: list[str] = []
    for c in rows:
        c = c or "未分类"
        if c not in seen:
            seen.append(c)
    return seen or ["未分类"]


@router.patch("/{image_id}")
def update_image(
    image_id: int, payload: ImageUpdate, session: Session = Depends(get_session)
):
    asset = session.get(ImageAsset, image_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    if payload.title is not None:
        asset.title = payload.title.strip()
    if payload.category is not None:
        asset.category = payload.category.strip() or "未分类"
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return serialize_image(asset)


@router.get("/{image_id}/file")
def serve(image_id: int, session: Session = Depends(get_session)):
    asset = session.get(ImageAsset, image_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    path = Path(asset.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return FileResponse(path, media_type=asset.content_type)


@router.delete("/{image_id}", status_code=204)
def delete(image_id: int, session: Session = Depends(get_session)):
    asset = session.get(ImageAsset, image_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")
    try:
        Path(asset.path).unlink(missing_ok=True)
    except OSError:
        pass
    session.delete(asset)
    session.commit()
