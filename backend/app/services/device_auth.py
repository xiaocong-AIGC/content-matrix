from hashlib import sha256
from secrets import compare_digest, token_urlsafe

from fastapi import HTTPException

from app.models.entities import Device


def issue_device_token() -> tuple[str, str]:
    token = token_urlsafe(32)
    return token, hash_device_token(token)


def hash_device_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def authenticate_device(device: Device | None, token: str | None) -> Device:
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not token or not device.token_hash:
        raise HTTPException(status_code=401, detail="设备令牌缺失")
    if not compare_digest(hash_device_token(token), device.token_hash):
        raise HTTPException(status_code=401, detail="设备令牌无效")
    return device
