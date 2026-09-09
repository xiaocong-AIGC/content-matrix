from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishMode(StrEnum):
    MANUAL_CONFIRM = "manual_confirm"
    AUTO_PUBLISH = "auto_publish"


class DeviceStatus(StrEnum):
    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"


class ContentStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    DISABLED = "disabled"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

ALLOWED_TRANSITIONS = {
    TaskStatus.QUEUED: {TaskStatus.LEASED, TaskStatus.CANCELLED},
    TaskStatus.LEASED: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_CONFIRMATION,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_CONFIRMATION: {
        TaskStatus.RUNNING,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
}

