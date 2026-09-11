from sqlmodel import Session, select

from app.models.entities import DeviceAccount, utcnow


def upsert_account(
    session: Session,
    device_id: int,
    platform: str,
    nickname: str | None = None,
    account_id: str | None = None,
    logged_in: bool | None = None,
) -> DeviceAccount:
    """Create or update a device's platform account. Operator-set fields
    (city / auto_publish / daily_quota / health) are preserved; only the
    agent-reported identity (nickname / account_id / logged_in) is refreshed when
    given. A logged-out report keeps the last-known nickname so the matrix still
    shows which 号 dropped offline."""
    acc = session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.device_id == device_id)
        .where(DeviceAccount.platform == platform)
    ).first()
    if not acc:
        acc = DeviceAccount(device_id=device_id, platform=platform)
    if nickname:
        acc.nickname = nickname
    if account_id:
        acc.account_id = account_id
    if logged_in is not None:
        acc.logged_in = logged_in
    acc.updated_at = utcnow()
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def get_account(
    session: Session, device_id: int, platform: str = "douyin"
) -> DeviceAccount:
    """Get the device's account for a platform, creating an empty one if missing
    (so operator actions on a not-yet-reported platform still work)."""
    acc = session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.device_id == device_id)
        .where(DeviceAccount.platform == platform)
    ).first()
    if acc:
        return acc
    return upsert_account(session, device_id, platform)


def accounts_for(session: Session, device_id: int) -> list[DeviceAccount]:
    return session.exec(
        select(DeviceAccount)
        .where(DeviceAccount.device_id == device_id)
        .order_by(DeviceAccount.platform)
    ).all()


def serialize_account(acc: DeviceAccount) -> dict:
    return {
        "id": acc.id,
        "device_id": acc.device_id,
        "platform": acc.platform,
        "nickname": acc.nickname,
        "account_id": acc.account_id,
        "city": acc.city or "未分组",
        "auto_publish": acc.auto_publish,
        # 账号例外：None = 跟随城市 / "on" / "off"
        "publish_override": acc.publish_override,
        # 群推送和发布是两个独立开关：有的号适合发群、有的不适合
        "auto_broadcast": acc.auto_broadcast,
        "daily_quota": acc.daily_quota,
        "health": acc.health,
        "health_message": acc.health_message,
        "logged_in": acc.logged_in,
    }
