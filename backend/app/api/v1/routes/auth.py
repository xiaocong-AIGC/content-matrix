from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_urlsafe

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.core.admin_auth import (
    Identity,
    current_identity,
    hash_console_token,
    require_admin,
    require_admin_role,
)
from app.core.config import get_settings
from app.core.serialization import normalize_datetimes
from app.db.session import get_session
from app.models.entities import AppSetting, ConsoleToken, RenewalRequest, utcnow
from app.schemas.dto import (
    RenewalApprove,
    RenewalReject,
    TokenCreate,
    TokenExtend,
    TokenFreeze,
)

router = APIRouter(prefix="/auth", tags=["auth"])

NOTE_KEY = "renewal_note"
_QR_KINDS = {"wechat": "微信", "alipay": "支付宝"}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _status(t: ConsoleToken) -> str:
    if t.frozen:
        return "frozen"
    exp = _as_utc(t.expires_at)
    if exp is not None and exp <= datetime.now(timezone.utc):
        return "expired"
    return "active"


def _days_left(t: ConsoleToken) -> int | None:
    exp = _as_utc(t.expires_at)
    if exp is None:
        return None  # 永久
    return (exp - datetime.now(timezone.utc)).days


def serialize_token(t: ConsoleToken) -> dict:
    return normalize_datetimes(
        {
            "id": t.id,
            "name": t.name,
            "role": t.role,
            "status": _status(t),
            "frozen": t.frozen,
            "expires_at": t.expires_at,
            "days_left": _days_left(t),
            "renewal_requested": t.renewal_requested,
            "last_used_at": t.last_used_at,
            "created_at": t.created_at,
        }
    )


def _qr_path(kind: str) -> Path:
    return get_settings().storage_dir / "renewal" / f"{kind}.png"


def _get_note(session: Session) -> str:
    row = session.get(AppSetting, NOTE_KEY)
    return row.value if row else ""


@router.get("/me")
def me(identity: Identity | None = Depends(current_identity)):
    """Who is logged in + their token status/expiry (works even if expired/frozen
    so the operator can see they need to renew). auth=false means auth disabled."""
    if identity is None:
        return {"name": "本地开发", "role": "admin", "status": "active", "auth": False}
    days = None
    if identity.expires_at is not None:
        delta = identity.expires_at - datetime.now(timezone.utc)
        days = delta.days
    return {
        "name": identity.name,
        "role": identity.role,
        "status": identity.status,
        "expires_at": normalize_datetimes({"e": identity.expires_at})["e"],
        "days_left": days,
        "renewal_requested": identity.renewal_requested,
        "auth": True,
    }


# ---- admin: token management ------------------------------------------------
@router.get("/tokens", dependencies=[Depends(require_admin_role)])
def list_tokens(session: Session = Depends(get_session)):
    rows = session.exec(
        select(ConsoleToken)
        .where(ConsoleToken.revoked == False)  # noqa: E712
        .order_by(ConsoleToken.created_at.desc())
    ).all()
    return [serialize_token(t) for t in rows]


@router.post("/tokens", status_code=201, dependencies=[Depends(require_admin_role)])
def create_token(payload: TokenCreate, session: Session = Depends(get_session)):
    """Generate a named token. valid_days=0/None → 永久. Raw value returned ONCE."""
    raw = token_urlsafe(24)
    expires = (
        datetime.now(timezone.utc) + timedelta(days=payload.valid_days)
        if payload.valid_days
        else None
    )
    row = ConsoleToken(
        name=payload.name.strip(),
        role=payload.role,
        token_hash=hash_console_token(raw),
        expires_at=expires,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**serialize_token(row), "token": raw}


@router.post("/tokens/{token_id}/extend", dependencies=[Depends(require_admin_role)])
def extend_token(
    token_id: int, payload: TokenExtend, session: Session = Depends(get_session)
):
    """延期：from the later of now / current expiry, add N days. Clears the
    pending-renewal flag."""
    row = session.get(ConsoleToken, token_id)
    if not row:
        raise HTTPException(status_code=404, detail="令牌不存在")
    base = _as_utc(row.expires_at)
    now = datetime.now(timezone.utc)
    base = base if base and base > now else now
    row.expires_at = base + timedelta(days=payload.days)
    row.renewal_requested = False
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_token(row)


@router.patch("/tokens/{token_id}/freeze", dependencies=[Depends(require_admin_role)])
def freeze_token(
    token_id: int, payload: TokenFreeze, session: Session = Depends(get_session)
):
    row = session.get(ConsoleToken, token_id)
    if not row:
        raise HTTPException(status_code=404, detail="令牌不存在")
    row.frozen = payload.frozen
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_token(row)


@router.delete("/tokens/{token_id}", status_code=204, dependencies=[Depends(require_admin_role)])
def delete_token(token_id: int, session: Session = Depends(get_session)):
    row = session.get(ConsoleToken, token_id)
    if not row:
        raise HTTPException(status_code=404, detail="令牌不存在")
    session.delete(row)
    session.commit()


# ---- renewal (收款) config: admin sets, operators view ----------------------
@router.put("/renewal-config", dependencies=[Depends(require_admin_role)])
async def set_renewal_config(
    note: str = Form(""),
    wechat_qr: UploadFile | None = File(default=None),
    alipay_qr: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
):
    """Admin sets the renewal note + WeChat/Alipay 收款码 images shown to operators."""
    row = session.get(AppSetting, NOTE_KEY)
    if row:
        row.value = note
        row.updated_at = utcnow()
    else:
        row = AppSetting(key=NOTE_KEY, value=note)
    session.add(row)
    session.commit()
    directory = get_settings().storage_dir / "renewal"
    directory.mkdir(parents=True, exist_ok=True)
    for kind, upload in (("wechat", wechat_qr), ("alipay", alipay_qr)):
        if upload is not None:
            data = await upload.read()
            if len(data) > get_settings().max_upload_bytes:
                raise HTTPException(status_code=413, detail="收款码图片过大")
            _qr_path(kind).write_bytes(data)
    return {"ok": True}


@router.get("/renewal-info")
def renewal_info(
    identity: Identity | None = Depends(current_identity),
    session: Session = Depends(get_session),
):
    """Operator-facing (works even if expired/frozen): the renewal note + which
    收款码 are available, so the user can pay and request activation."""
    return {
        "note": _get_note(session),
        "wechat": _qr_path("wechat").exists(),
        "alipay": _qr_path("alipay").exists(),
    }


@router.get("/renewal-qr/{kind}")
def renewal_qr(
    kind: str, identity: Identity | None = Depends(current_identity)
):
    if kind not in _QR_KINDS:
        raise HTTPException(status_code=404, detail="未知收款方式")
    path = _qr_path(kind)
    if not path.exists():
        raise HTTPException(status_code=404, detail="尚未配置收款码")
    return FileResponse(path, media_type="image/png")


def _serialize_request(r: RenewalRequest) -> dict:
    return normalize_datetimes(
        {
            "id": r.id,
            "token_id": r.token_id,
            "token_name": r.token_name,
            "amount": r.amount,
            "paid_at_text": r.paid_at_text,
            "reference": r.reference,
            "has_screenshot": bool(r.screenshot_path),
            "status": r.status,
            "review_note": r.review_note,
            "days_granted": r.days_granted,
            "created_at": r.created_at,
            "reviewed_at": r.reviewed_at,
        }
    )


@router.post("/request-renewal")
async def request_renewal(
    amount: str = Form(""),
    paid_at_text: str = Form(""),
    reference: str = Form(""),
    screenshot: UploadFile | None = File(default=None),
    identity: Identity | None = Depends(current_identity),
    session: Session = Depends(get_session),
):
    """Operator submits a 续费申请 WITH self-reported payment evidence. It is only a
    REQUEST — the admin must verify the money against their 收款记录 and approve.
    Nothing is auto-trusted / auto-extended."""
    if identity is None or identity.token_id is None:
        raise HTTPException(status_code=400, detail="主令牌无需续费")
    token = session.get(ConsoleToken, identity.token_id)
    if not token:
        raise HTTPException(status_code=404, detail="令牌不存在")
    shot_path: str | None = None
    if screenshot is not None:
        data = await screenshot.read()
        if len(data) > get_settings().max_upload_bytes:
            raise HTTPException(status_code=413, detail="截图过大")
        directory = get_settings().storage_dir / "renewals"
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{token.id}-{int(datetime.now(timezone.utc).timestamp())}.png"
        p.write_bytes(data)
        shot_path = str(p)
    req = RenewalRequest(
        token_id=token.id,
        token_name=token.name,
        amount=amount.strip()[:40],
        paid_at_text=paid_at_text.strip()[:40],
        reference=reference.strip()[:200],
        screenshot_path=shot_path,
    )
    session.add(req)
    token.renewal_requested = True  # quick badge; truth lives in RenewalRequest
    session.add(token)
    session.commit()
    session.refresh(req)
    return _serialize_request(req)


@router.get("/my-renewal")
def my_renewal(
    identity: Identity | None = Depends(current_identity),
    session: Session = Depends(get_session),
):
    """The caller's latest 续费申请 + its review status (so they see 待审核/通过/拒绝)."""
    if identity is None or identity.token_id is None:
        return None
    r = session.exec(
        select(RenewalRequest)
        .where(RenewalRequest.token_id == identity.token_id)
        .order_by(RenewalRequest.created_at.desc())
    ).first()
    return _serialize_request(r) if r else None


@router.get("/renewal-requests", dependencies=[Depends(require_admin_role)])
def list_renewal_requests(
    status: str = "pending", session: Session = Depends(get_session)
):
    stmt = select(RenewalRequest).order_by(RenewalRequest.created_at.desc())
    if status:
        stmt = stmt.where(RenewalRequest.status == status)
    return [_serialize_request(r) for r in session.exec(stmt).all()]


@router.get("/renewal-requests/{req_id}/screenshot", dependencies=[Depends(require_admin_role)])
def renewal_screenshot(req_id: int, session: Session = Depends(get_session)):
    r = session.get(RenewalRequest, req_id)
    if not r or not r.screenshot_path or not Path(r.screenshot_path).exists():
        raise HTTPException(status_code=404, detail="无截图")
    return FileResponse(r.screenshot_path, media_type="image/png")


def _clear_badge_if_done(session: Session, token_id: int) -> None:
    pending = session.exec(
        select(RenewalRequest)
        .where(RenewalRequest.token_id == token_id)
        .where(RenewalRequest.status == "pending")
    ).first()
    token = session.get(ConsoleToken, token_id)
    if token:
        token.renewal_requested = pending is not None
        session.add(token)


@router.post("/renewal-requests/{req_id}/approve", dependencies=[Depends(require_admin_role)])
def approve_renewal(
    req_id: int, payload: RenewalApprove, session: Session = Depends(get_session)
):
    """Admin verified the payment → extend the token by N days and mark approved."""
    r = session.get(RenewalRequest, req_id)
    if not r:
        raise HTTPException(status_code=404, detail="申请不存在")
    if r.status != "pending":
        raise HTTPException(status_code=409, detail="该申请已处理")
    token = session.get(ConsoleToken, r.token_id)
    if not token:
        raise HTTPException(status_code=404, detail="令牌不存在")
    base = _as_utc(token.expires_at)
    now = datetime.now(timezone.utc)
    base = base if base and base > now else now
    token.expires_at = base + timedelta(days=payload.days)
    r.status = "approved"
    r.days_granted = payload.days
    r.review_note = payload.note.strip()
    r.reviewed_at = now
    session.add(token)
    session.add(r)
    session.commit()
    _clear_badge_if_done(session, token.id)
    session.commit()
    return _serialize_request(r)


@router.post("/renewal-requests/{req_id}/reject", dependencies=[Depends(require_admin_role)])
def reject_renewal(
    req_id: int, payload: RenewalReject, session: Session = Depends(get_session)
):
    r = session.get(RenewalRequest, req_id)
    if not r:
        raise HTTPException(status_code=404, detail="申请不存在")
    if r.status != "pending":
        raise HTTPException(status_code=409, detail="该申请已处理")
    r.status = "rejected"
    r.review_note = payload.note.strip()
    r.reviewed_at = datetime.now(timezone.utc)
    session.add(r)
    session.commit()
    _clear_badge_if_done(session, r.token_id)
    session.commit()
    return _serialize_request(r)


# ── 首次配置向导 (first-run wizard) ──────────────────────────────────────────
# 分发/换电脑时: 新装的空系统需要把「你的管理员令牌」显示给本机操作员一次
# (打包版没有可见控制台看不到启动打印), 并让他填云机地址 + AI Key。
from fastapi import Request  # noqa: E402
from app.services import settings_store  # noqa: E402

_FIRSTRUN_FLAG = "firstrun_done"


@router.get("/bootstrap")
def bootstrap(request: Request, session: Session = Depends(get_session)):
    """本机(localhost)首次向导状态。fresh=True 时一次性返回 master 令牌供保存。
    做完点 /bootstrap-done 后不再返回。仅本机可读——远端(隧道)拿不到令牌。"""
    done = settings_store.get(session, _FIRSTRUN_FLAG) == "1"
    client = request.client.host if request.client else ""
    is_local = client in ("127.0.0.1", "::1", "localhost")
    fresh = (not done) and is_local
    return {
        "fresh": fresh,
        "master_token": get_settings().admin_token if fresh else "",
        "adb_endpoints": settings_store.get(session, "adb_endpoints"),
    }


@router.post("/bootstrap-done", dependencies=[Depends(require_admin)])
def bootstrap_done(session: Session = Depends(get_session)):
    settings_store.put(session, _FIRSTRUN_FLAG, "1")
    return {"ok": True}
