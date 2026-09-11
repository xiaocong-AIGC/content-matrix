from datetime import datetime, timezone

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PublishTask(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=120)
    platform: str = Field(default="douyin", max_length=16, index=True)  # douyin | xhs
    publish_type: str = Field(default="image_text", max_length=32)
    cover_title: str = Field(default="", max_length=100)
    publish_title: str = Field(default="", max_length=100)
    body: str = Field(default="", sa_column=Column(Text))
    topics_json: str = Field(default="[]", sa_column=Column(Text))
    media_json: str = Field(default="[]", sa_column=Column(Text))
    publish_mode: str = Field(default="auto_publish", max_length=32)
    priority: int = Field(default=50, ge=0, le=100, index=True)
    target_device_id: int | None = Field(
        default=None, foreign_key="device.id", index=True
    )
    content_id: int | None = Field(
        default=None, foreign_key="contentitem.id", index=True
    )
    # Group-message (群发) fields, used when publish_type == "group_message".
    image_path: str | None = Field(default=None, max_length=500)
    mention_all: bool = Field(default=False)
    target_groups_json: str = Field(default="[]", sa_column=Column(Text))
    scheduled_at: datetime | None = Field(default=None, index=True)
    status: str = Field(default="queued", index=True, max_length=32)
    current_step: str = Field(default="queued", max_length=64)
    progress: int = Field(default=0, ge=0, le=100)
    device_id: int | None = Field(default=None, foreign_key="device.id", index=True)
    lease_token: str | None = Field(default=None, index=True, max_length=64)
    lease_expires_at: datetime | None = Field(default=None, index=True)
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    # 进入 waiting_confirmation（需人工介入）的时刻。人工窗口必须从这里算，不能从
    # started_at 算 —— 后者是"任务开跑"的时刻，执行本身占掉多久，留给人的时间就少
    # 多久：线上 40 次超时里 38 次是在开跑后 600~660 秒被判死的，也就是说一个跑了
    # 9 分钟才转人工的任务，人只有 1 分钟；跑满 10 分钟的，窗口直接是 0。
    waiting_since: datetime | None = None
    finished_at: datetime | None = None


class Device(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    device_code: str = Field(unique=True, index=True, max_length=120)
    name: str = Field(max_length=120)
    platform: str = Field(default="android", max_length=32)
    agent_version: str = Field(default="unknown", max_length=32)
    android_version: str | None = Field(default=None, max_length=32)
    douyin_version: str | None = Field(default=None, max_length=32)
    douyin_nickname: str | None = Field(default=None, max_length=120)
    douyin_id: str | None = Field(default=None, max_length=64)
    city: str = Field(default="未分组", max_length=40, index=True)  # 城市矩阵分组
    # ⚠ 死字段：账号搬到 DeviceAccount 之后没有任何代码再读它（生产上 37 台全是 0）。
    # 真正生效的是 DeviceAccount.auto_publish，再往上是城市策略。别在这里改开关。
    auto_publish: bool = Field(default=False, index=True)
    daily_quota: int = Field(default=2, ge=0, le=50)
    health: str = Field(default="normal", index=True, max_length=16)
    health_message: str | None = Field(default=None, max_length=200)
    capabilities_json: str = Field(default="[]", sa_column=Column(Text))
    token_hash: str | None = Field(default=None, max_length=64)
    status: str = Field(default="online", index=True, max_length=32)
    # 无障碍是否真正绑定（安卓13 上设了却常绑不上，导致"在线却发不了"）。
    # null=未知/旧客户端, true=已绑定就绪, false=未绑定（不该派任务）。
    accessibility_ok: bool | None = Field(default=None)
    # 无障碍**从什么时候开始掉的**。没有这个时间戳就分不清「抖了一下」和
    # 「卡死了」—— 而这两件事对运营是完全不同的处置：前者不用管（发布失败会
    # 自动重排到今天后面的时段），后者才需要人去重启那台手机。
    # 2026-09-08 实测：当天 7 台掉过无障碍，6 台几分钟内自己回来了。
    accessibility_since: datetime | None = Field(default=None)
    current_task_id: int | None = Field(default=None, index=True)
    # Operator clicked "更新数据" on 效果榜 — the agent re-runs 回采 on its next
    # idle heartbeat, then the flag is consumed (set False) by the heartbeat reply.
    metrics_refresh_requested: bool = Field(default=False)
    # 操作员点了设备卡片「更新账号」→ Agent 下次心跳强制重读本机登录的账号。
    account_refresh_requested: bool = Field(default=False)
    last_heartbeat_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DeviceAccount(SQLModel, table=True):
    """A platform account bound to a device. One physical phone (Device) can run
    multiple platform accounts (e.g. douyin + xhs). The per-account knobs
    (city / auto-publish / quota / health) live here, not on Device."""

    id: int | None = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", index=True)
    platform: str = Field(default="douyin", max_length=16, index=True)  # douyin | xhs
    nickname: str | None = Field(default=None, max_length=120)
    account_id: str | None = Field(default=None, max_length=64)  # 抖音号 / 小红书号
    city: str = Field(default="未分组", max_length=40, index=True)
    # ⚠ 城市设了自动发布开关之后，这个老开关就不再起作用了 —— 它只在
    # 「城市没设开关」时作兜底（见 services/city_policy.publishes）。
    auto_publish: bool = Field(default=False, index=True)
    # 账号例外：None = 跟随城市（默认），"on" = 强制开，"off" = 强制关。
    # 用来处理「全城开着，但这个号今天有问题先停一下」这种单点情况，
    # 不用为了一个号去动整个城市。
    publish_override: str | None = Field(default=None, max_length=8)
    daily_quota: int = Field(default=2, ge=0, le=50)
    health: str = Field(default="normal", index=True, max_length=16)
    health_message: str | None = Field(default=None, max_length=200)
    # 群推送单独一个开关：有的号适合发群、有的不适合，不能跟着发布走
    auto_broadcast: bool = Field(default=False, index=True)
    # 登录态：true=已登录, false=检测到登录页(未登录), null=未知/未探测。
    logged_in: bool | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExecutionLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="publishtask.id", index=True)
    device_id: int | None = Field(default=None, foreign_key="device.id", index=True)
    level: str = Field(default="info", index=True, max_length=16)
    step: str = Field(default="unknown", index=True, max_length=64)
    message: str = Field(sa_column=Column(Text))
    context_json: str = Field(default="{}", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ContentItem(SQLModel, table=True):
    """A reusable piece of content in the shared pool, consumed exactly once."""

    id: int | None = Field(default=None, primary_key=True)
    cover_title: str = Field(default="", max_length=200)  # 大字报封面文字
    title: str = Field(default="", max_length=100)  # 发布页标题（选填）
    body: str = Field(default="", sa_column=Column(Text))  # 正文/文案
    topics_json: str = Field(default="[]", sa_column=Column(Text))
    # 城市：空/通用 = 任意账号可用；指定城市 = 只发给该城市的账号（矩阵化）。
    city: str = Field(default="通用", max_length=40, index=True)
    # 目标平台：douyin | xhs | both（both = 先小红书成功再发抖音，串行）。
    platform: str = Field(default="douyin", max_length=16, index=True)
    status: str = Field(default="pending", index=True, max_length=16)
    published_device_id: int | None = Field(
        default=None, foreign_key="device.id", index=True
    )
    published_task_id: int | None = Field(default=None, index=True)
    published_at: datetime | None = None
    # 从哪条草稿来的、那条草稿用的哪版提示词。人工新建/CSV 导入的内容
    # 两个都是空 —— 分析时正是靠这个把「机器写的」和「人写的」分开。
    draft_id: int | None = Field(default=None, index=True)
    prompt_version_id: int | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class ContentDraft(SQLModel, table=True):
    """An AI-generated content candidate (DeepSeek 二改). Drafts wait for human
    review; accepting one creates a real ContentItem in the pool. Never published
    directly — keeps the library curated."""

    id: int | None = Field(default=None, primary_key=True)
    cover_title: str = Field(default="", max_length=200)  # 大字报封面文字
    title: str = Field(default="", max_length=100)
    body: str = Field(default="", sa_column=Column(Text))
    topics_json: str = Field(default="[]", sa_column=Column(Text))
    platform: str = Field(default="douyin", max_length=16, index=True)
    city: str = Field(default="通用", max_length=40)
    # Which 高互动 post(s) this was remixed from, + the model's structural notes.
    source_content_id: int | None = Field(default=None, index=True)
    source_note: str = Field(default="", sa_column=Column(Text))
    model: str = Field(default="", max_length=40)
    status: str = Field(default="pending", index=True, max_length=16)  # pending|accepted|rejected|duplicate
    accepted_content_id: int | None = Field(default=None, index=True)
    # 成本记账：本草稿分摊到的 token 与人民币成本（整批 usage / 草稿数）。
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    cost_cny: float = Field(default=0.0)
    # 查重：与已有内容/草稿正文的最高相似度(0~1)，及最相似的来源描述。
    dup_score: float = Field(default=0.0)
    dup_of: str = Field(default="", max_length=200)
    # 哪一版提示词写的 —— 没有它，「改了提示词有没有变好」永远无法验证。
    prompt_version_id: int | None = Field(default=None, index=True)
    # 人第一次动这条草稿之前的原文。运营改过之后再拿它评价提示词，
    # 评的就是「模型 + 人」的合成结果，不是提示词本身。
    original_body: str = Field(default="", sa_column=Column(Text))
    # 为什么被拒。以前机器闸的理由挤在 dup_of 里（那栏本来是查重来源），
    # 人工拒绝则什么都不记 —— 于是「模型老犯什么错」无从统计。
    reject_reason: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class PromptVersion(SQLModel, table=True):
    """一次生成**实际用过**的 system prompt 的不可变快照。

    按正文的 sha256 寻址：同一段提示词只存一行，改一个字就是新的一行。

    ⚠ 为什么不是「版本表 + 一根活动指针」：这张表的全部价值是回答
    「这条内容是哪版提示词写的」。指针记的是「谁声明当前用哪版」，会和现实
    漂开（有预设、有全局、还有人临时改一版就生成一批）；按内容寻址记的是
    **真正发出去的那段话**，漂不开。副作用也是对的：全局提示词和某个预设
    的文案一模一样时，它们本来就是同一版，共用一行。

    今天这条链是断的 —— 草稿不记用了哪段提示词，于是「上周改的那句到底有没有
    用」永远无法回答。这是盘点出的 13 处断点里**唯一时间不可逆**的一处：
    没记下来的那些，事后补不回来。
    """

    id: int | None = Field(default=None, primary_key=True)
    sha256: str = Field(max_length=64, index=True, unique=True)
    text: str = Field(default="", sa_column=Column(Text))
    # 第一次见到它是从哪来的："global" 或 "preset:12"。仅供人看，不参与身份。
    first_seen_in: str = Field(default="global", max_length=40)
    # 人给的名字，比如「加了痛点开头那版」。可空，随时能补。
    label: str = Field(default="", max_length=120)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class CityPolicy(SQLModel, table=True):
    """一个城市的自动发布策略。每个字段都可以为空 —— **空 = 这一项跟随全局**。

    所以一个城市可以只单独设时段，开关和篇数继续走全局。解析规则全在
    `services/city_policy.py`，别在别处直读这张表。
    """

    id: int | None = Field(default=None, primary_key=True)
    city: str = Field(max_length=40, index=True, unique=True)
    # None = 城市不管开关，各号按自己的老开关走；True/False = 全城统一
    auto_publish: bool | None = Field(default=None)
    # None = 用全局时段；"" = 这个城市明确不限时段；"10-11,14-15" = 城市自己的
    windows: str | None = Field(default=None, max_length=200)
    # None = 用全局篇数
    daily_target: int | None = Field(default=None, ge=1, le=20)
    updated_at: datetime = Field(default_factory=utcnow)


class ConsoleToken(SQLModel, table=True):
    """A named console access token (an operator/admin 账号). The raw token is
    shown once at creation; only its sha256 hash is stored. The env/persisted
    master token always works and bootstraps the first one."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=60, index=True)  # 账号名/备注
    token_hash: str = Field(unique=True, index=True, max_length=64)
    role: str = Field(default="operator", max_length=16)  # admin | operator
    revoked: bool = Field(default=False, index=True)
    frozen: bool = Field(default=False, index=True)  # 冻结：临时停用，可解冻
    # 有效期：None = 永久（admin/master）。过期后令牌失效，operator 可自助续费。
    expires_at: datetime | None = Field(default=None, index=True)
    renewal_requested: bool = Field(default=False, index=True)  # 用户已付款待开通
    last_used_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class RenewalRequest(SQLModel, table=True):
    """A续费申请 submitted by an operator with self-reported payment evidence.
    The admin verifies it against their actual 收款记录 and approves (→ extends the
    token) or rejects. NOTHING is auto-trusted — approval is always a human act."""

    id: int | None = Field(default=None, primary_key=True)
    token_id: int = Field(foreign_key="consoletoken.id", index=True)
    token_name: str = Field(default="", max_length=60)  # snapshot for the review list
    amount: str = Field(default="", max_length=40)       # 用户填写的付款金额
    paid_at_text: str = Field(default="", max_length=40)  # 用户填写的付款时间（文本）
    reference: str = Field(default="", max_length=200)    # 付款单号 / 备注
    screenshot_path: str | None = Field(default=None, max_length=500)
    status: str = Field(default="pending", index=True, max_length=16)  # pending|approved|rejected
    review_note: str = Field(default="", sa_column=Column(Text))       # 拒绝原因 / 备注
    days_granted: int | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    reviewed_at: datetime | None = None


class AppSetting(SQLModel, table=True):
    """Generic runtime-editable settings (key→value). First use: the editable
    内容引擎 system prompt so the operator can tune the AI's instructions live."""

    key: str = Field(primary_key=True, max_length=64)
    value: str = Field(default="", sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=utcnow)


class PromptPreset(SQLModel, table=True):
    """A saved AI-generation preset: a system prompt + a sample source. Operators can
    keep several and pick one per generation. sample_mode:
      default — use the current top-performer 回采 logic (existing behaviour)
      custom  — use `sample_text` (operator-pasted/uploaded raw samples) verbatim
      pick    — use the library ContentItems in `sample_ids_json`
    `owner` = operator name ("" = shared/built by master, visible to all)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=80)
    owner: str = Field(default="", max_length=80, index=True)
    system_prompt: str = Field(default="", sa_column=Column(Text))
    sample_mode: str = Field(default="default", max_length=16)
    sample_text: str = Field(default="", sa_column=Column(Text))
    sample_ids_json: str = Field(default="[]", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ImageAsset(SQLModel, table=True):
    """An image uploaded to the shared library, reusable across broadcasts/posts."""

    id: int | None = Field(default=None, primary_key=True)
    filename: str = Field(default="image", max_length=200)
    title: str = Field(default="", max_length=120)  # operator-facing display name
    category: str = Field(default="未分类", max_length=60, index=True)
    path: str = Field(max_length=500)
    content_type: str = Field(default="image/jpeg", max_length=80)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class UploadAudit(SQLModel, table=True):
    """Trace of every image upload (no size limit, but fully auditable)."""

    id: int | None = Field(default=None, primary_key=True)
    image_id: int | None = Field(default=None, index=True)
    filename: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=60)
    size_bytes: int = Field(default=0)
    content_type: str = Field(default="", max_length=80)
    client_ip: str = Field(default="", max_length=64, index=True)
    user_agent: str = Field(default="", max_length=300)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class PostMetric(SQLModel, table=True):
    """A snapshot of a published post's public metrics (回采). One row per
    回采 per post, so we keep a time-series. Linked to the publish task/content
    when matched, else kept at account level."""

    id: int | None = Field(default=None, primary_key=True)
    task_id: int | None = Field(default=None, foreign_key="publishtask.id", index=True)
    content_id: int | None = Field(default=None, index=True)
    device_id: int | None = Field(default=None, index=True)
    platform: str = Field(default="douyin", max_length=16, index=True)
    account_id: str | None = Field(default=None, max_length=64, index=True)
    title: str = Field(default="", max_length=200)  # the matched note's title/first line
    body: str = Field(default="", sa_column=Column(Text))  # 回采到的正文/caption（喂给二改）
    views: int = Field(default=0)      # 阅读/播放
    likes: int = Field(default=0)      # 点赞
    collects: int = Field(default=0)   # 收藏
    comments: int = Field(default=0)   # 评论
    shares: int = Field(default=0)     # 转发/分享
    captured_at: datetime = Field(default_factory=utcnow, index=True)


class ChatGroup(SQLModel, table=True):
    """A Douyin group chat the agent found on a device, for 群发 targeting."""

    id: int | None = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="device.id", index=True)
    group_name: str = Field(index=True, max_length=120)
    member_count: int | None = None
    can_mention_all: bool = Field(default=False)
    last_seen_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)


class BroadcastMessage(SQLModel, table=True):
    """群推送消息库里的一条。

    **可以反复用**，这一点和作品池 `ContentItem` 正好相反：ContentItem 是"被某条任务
    消费掉就没了"（有 published_task_id），而同一条群消息要发给多个号、多个群、
    很可能每天都发。所以它不能塞进 ContentItem，得自己一张表。

    `city` 为空或"通用"表示哪个号都能用；填了城市就只给该城市的号用
    （和内容库一个规矩，运营不用记两套）。
    """

    id: int | None = Field(default=None, primary_key=True)
    text: str = Field(default="", sa_column=Column(Text))
    image_path: str | None = Field(default=None, max_length=500)
    mention_all: bool = Field(default=False)
    city: str = Field(default="通用", max_length=40, index=True)
    enabled: bool = Field(default=True, index=True)
    # 轮换时优先挑发得少的，避免同一条被反复推
    sent_count: int = Field(default=0)
    last_sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class Screenshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="publishtask.id", index=True)
    device_id: int | None = Field(default=None, foreign_key="device.id", index=True)
    step: str = Field(default="unknown", index=True, max_length=64)
    file_path: str = Field(max_length=500)
    content_type: str = Field(default="image/png", max_length=80)
    width: int | None = None
    height: int | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class NotifyRoute(SQLModel, table=True):
    """城市 → 收件渠道。转人工/掉线这类告警按内容所属城市路由到对应的群或邮箱。

    `city` 取具体城市名；`*` 是兜底（没配到的城市走它）；`__global__` 是系统级
    （续费申请、通道自身故障这类和城市无关的）。
    """

    id: int | None = Field(default=None, primary_key=True)
    city: str = Field(default="*", index=True, max_length=40)
    channel: str = Field(default="wecom", max_length=16)  # wecom | email
    target: str = Field(default="", max_length=500)  # webhook URL / 收件人
    oncall_mobiles: str = Field(default="", max_length=500)  # 企微 @人 的手机号，逗号分隔
    min_severity: str = Field(default="warning", max_length=16)  # info|warning|critical
    quiet_hours: str = Field(default="", max_length=32)  # 例 "23:00-08:00"，空=不静默
    daily_cap: int = Field(default=200)
    attach_screenshot: bool = Field(default=False)
    enabled: bool = Field(default=True, index=True)
    consecutive_failures: int = Field(default=0)
    last_error: str | None = Field(default=None, max_length=300)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AlertDispatch(SQLModel, table=True):
    """发送台账：去重 + 重试 + 审计。

    ⚠ 写这张表的 `enqueue()` **必须开自己的 Session 并吞掉一切异常**。它被
    `update_agent_status` 这类跑在 Agent 请求线程里的代码调用；一旦 dedup_key 撞
    唯一约束抛 IntegrityError 而共用了调用方的 session，SQLAlchemy 会把整个 session
    置成 rollback-only，Agent 的终态写入随之失败 → 任务永远停在 running、永远续租、
    永不过期 → **这台机再也领不到任务**。等于通知系统的一个唯一约束获得了停掉一台
    设备的能力。
    """

    id: int | None = Field(default=None, primary_key=True)
    dedup_key: str = Field(unique=True, index=True, max_length=200)
    event: str = Field(index=True, max_length=48)
    severity: str = Field(default="warning", index=True, max_length=16)
    city: str = Field(default="", index=True, max_length=40)
    device_id: int | None = Field(default=None, index=True)
    account_id: int | None = Field(default=None)
    task_id: int | None = Field(default=None, index=True)
    screenshot_id: int | None = Field(default=None)
    title: str = Field(default="", max_length=200)
    payload_json: str = Field(default="{}", sa_column=Column(Text))
    route_id: int | None = Field(default=None)
    channel: str = Field(default="", max_length=16)
    # pending | sent | failed | no_route | suppressed
    status: str = Field(default="pending", index=True, max_length=16)
    attempts: int = Field(default=0)
    next_retry_at: datetime | None = Field(default=None, index=True)
    last_error: str | None = Field(default=None, max_length=300)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    sent_at: datetime | None = None
