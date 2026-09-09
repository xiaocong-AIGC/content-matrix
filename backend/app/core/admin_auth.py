import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi import Depends, Header, HTTPException, Query
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.session import get_session
from app.models.entities import ConsoleToken


def hash_console_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Identity:
    """Who is calling the console. status ∈ active | frozen | expired (master is
    always active admin). Carries expiry so operators can see/renew it."""

    def __init__(
        self,
        name: str,
        role: str,
        status: str = "active",
        token_id: int | None = None,
        expires_at: datetime | None = None,
        renewal_requested: bool = False,
    ):
        self.name = name
        self.role = role
        self.status = status
        self.token_id = token_id
        self.expires_at = expires_at
        self.renewal_requested = renewal_requested


def _row_status(row: ConsoleToken) -> str:
    if row.frozen:
        return "frozen"
    exp = _as_utc(row.expires_at)
    if exp is not None and exp <= datetime.now(timezone.utc):
        return "expired"
    return "active"


def _resolve(provided: str, session: Session) -> Identity | None:
    """Identify a token regardless of active/frozen/expired (None = unknown)."""
    master = get_settings().admin_token
    if provided and master and secrets.compare_digest(provided, master):
        return Identity("主令牌", "admin", "active")
    if not provided:
        return None
    row = session.exec(
        select(ConsoleToken).where(
            ConsoleToken.token_hash == hash_console_token(provided),
            ConsoleToken.revoked == False,  # noqa: E712
        )
    ).first()
    if not row:
        return None
    return Identity(
        row.name, row.role, _row_status(row), row.id,
        _as_utc(row.expires_at), row.renewal_requested,
    )


def _touch_last_used(session: Session, token_id: int) -> None:
    # Throttle writes (~5 min) to avoid amplification from the console polling.
    row = session.get(ConsoleToken, token_id)
    if not row:
        return
    now = datetime.now(timezone.utc)
    last = _as_utc(row.last_used_at)
    if last is None or now - last > timedelta(minutes=5):
        row.last_used_at = now
        session.add(row)
        session.commit()


def current_identity(
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Identity | None:
    """Lenient guard: returns the identity even if frozen/expired (so operators
    can view their status + renew). Only rejects unknown/wrong tokens. None when
    auth is disabled (no master configured)."""
    if not get_settings().admin_token:
        return None
    ident = _resolve(x_admin_token or token or "", session)
    if ident is None:
        raise HTTPException(status_code=401, detail="未授权：缺少或错误的令牌")
    return ident


def require_admin(
    identity: Identity | None = Depends(current_identity),
    session: Session = Depends(get_session),
) -> Identity | None:
    """Strict guard for the console API: token must be ACTIVE (not frozen/expired)."""
    if identity is None:
        return None  # auth disabled
    if identity.status == "frozen":
        raise HTTPException(status_code=403, detail="令牌已被冻结，请联系管理员")
    if identity.status == "expired":
        raise HTTPException(status_code=403, detail="令牌已过期，请续费延期")
    if identity.token_id is not None:
        _touch_last_used(session, identity.token_id)
    return identity


def require_admin_role(identity: Identity | None = Depends(require_admin)) -> Identity | None:
    """Master or an active admin-role token only (for token management)."""
    if identity is None:
        return None
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员角色")
    return identity


def require_provision_key(
    x_provision_key: str | None = Header(default=None),
) -> None:
    """Guard for device registration. Empty configured key = open (dev). Set
    PROVISION_KEY in prod so only provisioned phones can register into the matrix."""
    expected = get_settings().provision_key
    if not expected:
        return
    if not secrets.compare_digest(x_provision_key or "", expected):
        raise HTTPException(status_code=401, detail="未授权：设备注册密钥无效")
