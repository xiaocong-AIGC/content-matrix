from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: str = "douyin"  # douyin | xhs
    publish_type: str = "image_text"
    cover_title: str = Field(default="", max_length=100)
    publish_title: str = Field(default="", max_length=100)
    body: str = Field(default="", max_length=5000)
    topics: list[str] = Field(default_factory=list, max_length=20)
    media: list[str] = Field(default_factory=list)
    publish_mode: str = "auto_publish"
    priority: int = Field(default=50, ge=0, le=100)
    target_device_id: int = Field(description="必须指定执行设备（抖音号）")
    scheduled_at: datetime | None = None

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, topics: list[str]) -> list[str]:
        normalized = []
        for topic in topics:
            value = topic.strip().removeprefix("#")
            if value and value not in normalized:
                normalized.append(value)
        return normalized


class DeviceRegister(BaseModel):
    device_code: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    platform: str = "android"
    agent_version: str = "0.1.0"
    android_version: str | None = None
    douyin_version: str | None = None
    douyin_nickname: str | None = None
    douyin_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class DeviceHeartbeat(BaseModel):
    status: str = "online"
    current_task_id: int | None = None
    accessibility_ok: bool | None = None  # 无障碍是否已绑定（就绪判断）
    agent_version: str | None = None
    douyin_version: str | None = None
    douyin_nickname: str | None = None
    douyin_id: str | None = None
    health: str | None = None
    health_message: str | None = None
    platform: str = "douyin"  # which platform account a health update applies to


class ClaimRequest(BaseModel):
    device_id: int


class AgentStatusUpdate(BaseModel):
    device_id: int
    lease_token: str
    status: str
    step: str
    progress: int = Field(ge=0, le=100)
    message: str | None = None
    error_message: str | None = None


class AgentLogCreate(BaseModel):
    device_id: int
    lease_token: str
    level: str = "info"
    step: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class LeaseRenewRequest(BaseModel):
    device_id: int
    lease_token: str


class TaskAction(BaseModel):
    action: str
    message: str | None = None


class ContentCreate(BaseModel):
    cover_title: str = Field(default="", max_length=200)  # 大字报封面文字
    title: str = Field(default="", max_length=100)  # 发布页标题（选填）
    body: str = Field(min_length=1, max_length=5000)  # 正文/文案
    topics: list[str] = Field(default_factory=list, max_length=20)
    city: str = Field(default="通用", max_length=40)  # 通用 = 任意城市账号可用
    platform: str = Field(default="douyin", max_length=16)  # douyin | xhs | both

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, topics: list[str]) -> list[str]:
        normalized: list[str] = []
        for topic in topics:
            value = topic.strip().removeprefix("#")
            if value and value not in normalized:
                normalized.append(value)
        return normalized


class ContentUpdate(BaseModel):
    cover_title: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=100)
    body: str | None = Field(default=None, max_length=5000)
    topics: list[str] | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=40)
    platform: str | None = Field(default=None, max_length=16)
    status: str | None = None


class GenerateDraftsRequest(BaseModel):
    """Trigger DeepSeek 二改: remix our top posts into `count` new drafts."""

    count: int = Field(default=3, ge=1, le=10)
    platform: str = Field(default="xhs", max_length=16)  # douyin | xhs | both
    theme: str | None = Field(default=None, max_length=200)  # optional 选题方向
    source_content_ids: list[int] = Field(default_factory=list, max_length=5)
    preset_id: int | None = None  # 选用某套提示词/样本预设(优先于 source_content_ids)


class TokenCreate(BaseModel):
    """Create a named console access token (账号)."""

    name: str = Field(min_length=1, max_length=60)
    role: str = Field(default="operator")  # admin | operator
    valid_days: int | None = Field(default=30, ge=0, le=3650)  # 0/None = 永久

    @field_validator("role")
    @classmethod
    def valid_role(cls, role: str) -> str:
        return role if role in {"admin", "operator"} else "operator"


class TokenExtend(BaseModel):
    days: int = Field(ge=1, le=3650)


class TokenFreeze(BaseModel):
    frozen: bool


class RenewalConfigUpdate(BaseModel):
    note: str = Field(default="", max_length=500)


class RenewalApprove(BaseModel):
    days: int = Field(ge=1, le=3650)
    note: str = Field(default="", max_length=300)


class RenewalReject(BaseModel):
    note: str = Field(default="", max_length=300)


class PromptUpdate(BaseModel):
    """Set the 内容引擎 system prompt; blank resets to the built-in default."""

    prompt: str = Field(default="", max_length=8000)


class PromptPresetUpsert(BaseModel):
    """Create/update a saved AI-generation preset (a system prompt + sample source)."""

    name: str = Field(max_length=80)
    system_prompt: str = Field(default="", max_length=8000)
    sample_mode: str = Field(default="default", max_length=16)  # default|custom|pick
    sample_text: str = Field(default="", max_length=20000)
    sample_ids: list[int] = Field(default_factory=list, max_length=10)


class AiConfigUpdate(BaseModel):
    """Admin AI provider config. None = leave unchanged; "" = clear (env fallback)."""

    api_key: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=80)


class DraftUpdate(BaseModel):
    cover_title: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=100)
    body: str | None = Field(default=None, max_length=5000)
    topics: list[str] | None = Field(default=None, max_length=20)
    platform: str | None = Field(default=None, max_length=16)


class DraftAccept(BaseModel):
    """Accept a draft into the content pool, optionally tagging a city."""

    city: str = Field(default="通用", max_length=40)


class DeviceAutoPublishUpdate(BaseModel):
    auto_publish: bool
    daily_quota: int | None = Field(default=None, ge=0, le=50)
    platform: str = "douyin"

class DeviceAutoBroadcastUpdate(BaseModel):
    """开/关某个号的自动群推送。和发布的开关分开，故意不合并。"""

    auto_broadcast: bool
    platform: str = "douyin"




class SchedulePostsRequest(BaseModel):
    # Arbitrary day+time slots across any days; per-day quota is enforced
    # server-side. Capped generously to keep one request bounded.
    times: list[datetime] = Field(min_length=1, max_length=60)
    platform: str = "douyin"
    # Optional, parallel to `times`: pin a specific content_id to a slot (null =
    # 随机从内容库取). A pinned piece is locked out of other accounts once排期.
    content_ids: list[int | None] | None = None


class BatchScheduleRequest(BaseModel):
    # 跨账号批量排期: apply the SAME time-slots to every selected account. Content is
    # auto-assigned at random and de-duplicated across accounts (each consume locks
    # its piece), so no two accounts get the same content. Always random — pinning a
    # specific piece to many accounts makes no sense here.
    device_ids: list[int] = Field(min_length=1, max_length=200)
    platform: str = "douyin"
    times: list[datetime] = Field(min_length=1, max_length=60)


class DeviceHealthUpdate(BaseModel):
    health: str  # normal | restricted | banned | verify | abnormal
    health_message: str | None = Field(default=None, max_length=200)
    platform: str = "douyin"


class DeviceProfileUpdate(BaseModel):
    city: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=120)
    platform: str = "douyin"


class AccountInfo(BaseModel):
    platform: str = Field(max_length=16)  # douyin | xhs
    nickname: str | None = None
    account_id: str | None = None
    logged_in: bool | None = None  # true=已登录, false=检测到登录页(未登录), null=未知


class AccountReport(BaseModel):
    device_id: int
    accounts: list[AccountInfo] = Field(default_factory=list, max_length=10)


class PostMetricItem(BaseModel):
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=3000)  # 详情页正文/caption（供二改）
    views: int = 0
    likes: int = 0
    collects: int = 0
    comments: int = 0
    shares: int = 0


class MetricReport(BaseModel):
    device_id: int
    platform: str = "douyin"
    posts: list[PostMetricItem] = Field(default_factory=list, max_length=50)


class GroupInfo(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    member_count: int | None = None
    can_mention_all: bool = False


class GroupSync(BaseModel):
    device_id: int
    groups: list[GroupInfo] = Field(default_factory=list)


class BroadcastCreate(BaseModel):
    target_device_id: int
    text: str = Field(min_length=1, max_length=2000)
    image_id: int | None = None
    mention_all: bool = False
    groups: list[str] = Field(min_length=1, max_length=50)
    scheduled_at: datetime | None = None


class AdbEndpointsUpdate(BaseModel):
    """云机 adb 网络地址, 逗号分隔, 支持端口范围 (192.168.1.200:6001-6020)。"""

    endpoints: str = Field(default="", max_length=2000)
