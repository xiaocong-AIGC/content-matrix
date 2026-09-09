import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Douyin Mobile Publisher Agent"
    database_url: str = "sqlite:///./data/agent.db"
    storage_dir: Path = Path("./storage")
    # Origins allowed to call the API cross-origin. Comma-separated, or "*" for
    # LAN/internal. Dev uses Vite's proxy (same-origin) so this rarely matters.
    frontend_origin: str = "http://127.0.0.1:5173"
    # 多设备并发时，单编排器要轮流给每台手机 adb 拉起 App，启动可能排队较久；
    # 120s 容易在 20 台同时发布时把"还没轮到"的任务判超时。放宽到 300s。
    lease_seconds: int = 300
    # 全矩阵同时进行的自动发布任务上限（错峰）：单台 PC/编排器串行用 adb 拉 App，
    # 20 台一起发会过载 + 同时段规律行为。限并发，发完一批再放下一批。
    max_concurrent_publishes: int = 3
    device_offline_seconds: int = 45
    # A task parked in waiting_confirmation (hit an unknown/verify page) holds the
    # one-job-per-phone device. If no human resolves it within this window, it is
    # auto-failed so the auto-publish queue (and 群发) can continue. 0 disables.
    # 人工介入窗口。从「转人工」那一刻算起（见 fail_stuck_confirmations 的
    # waiting_since），不是从任务开跑算 —— 两者混用时这个值等于"执行+等人"的总预算。
    confirmation_timeout_seconds: int = 300
    # Auto-purge finished tasks (+ their logs/screenshots/files) older than this,
    # so the DB and disk don't grow unbounded. 0 disables.
    retention_days: int = 30

    # 邮件通道。口令一律走环境变量/.env，**不要进库** —— NotifyRoute 里只存收件人。
    # 465=SSL，587=STARTTLS。smtp_host 为空时邮件通道直接报"未配置"，不会静默失败。
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # Console (management API) auth. If ADMIN_TOKEN env is unset, a random token
    # is generated + persisted on first run (printed once) — no shipped default
    # to guess. Set ADMIN_TOKEN env to "" to explicitly disable auth (dev only).
    admin_token: str = ""
    # Pre-shared key required to register a new device/agent. Same treatment as
    # admin_token: generated+persisted if PROVISION_KEY env is unset, so device
    # registration is NOT open by default. The provisioning tools read the
    # persisted key from the storage dir automatically.
    provision_key: str = ""
    # Max bytes accepted per uploaded image / screenshot (bounds disk-fill DoS).
    max_upload_bytes: int = 20 * 1024 * 1024
    # DeepSeek (内容引擎). Key MUST come from env DEEPSEEK_API_KEY — never commit
    # it. Empty = generation disabled (the API returns a clear 400).
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    # 计价（人民币/百万 token），用于估算每批二改的成本。可按实际套餐用环境变量覆盖
    # （DEEPSEEK_PRICE_IN / DEEPSEEK_PRICE_OUT）。
    deepseek_price_in: float = 1.0
    deepseek_price_out: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _resolve_secret(env_name: str, storage_dir: Path, filename: str, label: str) -> str:
    """Return a secret: the env var if explicitly set (even to ""), else a
    per-install random value persisted under the storage dir (generated + printed
    once). Removes the 'known default token' risk."""
    if env_name in os.environ:
        return os.environ[env_name]  # operator's explicit choice (incl. "")
    path = storage_dir / filename
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    value = secrets.token_urlsafe(24)
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    print(
        f"[SECURITY] Generated {label}: {value}\n"
        f"           (persisted to {path}; override with the {env_name} env var)",
        flush=True,
    )
    return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.admin_token = _resolve_secret(
        "ADMIN_TOKEN", settings.storage_dir, ".admin_token", "管理后台令牌 ADMIN_TOKEN"
    )
    settings.provision_key = _resolve_secret(
        "PROVISION_KEY", settings.storage_dir, ".provision_key", "设备注册密钥 PROVISION_KEY"
    )
    return settings
