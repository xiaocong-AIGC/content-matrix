from datetime import datetime
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

settings = get_settings()

IS_SQLITE = settings.database_url.startswith("sqlite")

if IS_SQLITE and settings.database_url.startswith("sqlite:///"):
    database_path = Path(settings.database_url.removeprefix("sqlite:///"))
    database_path.parent.mkdir(parents=True, exist_ok=True)

if IS_SQLITE:
    # Single-file dev/small deployments. check_same_thread off so the FastAPI
    # threadpool can share the connection.
    from sqlalchemy.pool import QueuePool

    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False, "timeout": 10},
        poolclass=QueuePool,
        pool_size=30,
        max_overflow=20,
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        # WAL lets reads run concurrently with a writer and improves multi-client
        # throughput; busy_timeout makes writers wait briefly instead of erroring
        # with "database is locked". (Still single-writer — use Postgres at scale.)
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
else:
    # Postgres (recommended for multi-phone / multi-worker). A real pool with
    # pre-ping survives dropped connections; size for the worker count.
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    # The ad-hoc ALTERs below evolve a pre-existing SQLite dev DB. On Postgres a
    # fresh create_all already has every column, so skip them (use Alembic for
    # real Postgres schema changes).
    if not IS_SQLITE:
        return
    inspector = inspect(engine)
    device_columns = {column["name"] for column in inspector.get_columns("device")}
    if "token_hash" not in device_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE device ADD COLUMN token_hash VARCHAR(64)"))
    task_columns = {column["name"] for column in inspector.get_columns("publishtask")}
    if "target_device_id" not in task_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE publishtask ADD COLUMN target_device_id INTEGER")
            )
    if "content_id" not in task_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE publishtask ADD COLUMN content_id INTEGER")
            )
    task_extra = {
        "waiting_since": "DATETIME",
        "image_path": "VARCHAR(500)",
        "mention_all": "BOOLEAN DEFAULT 0",
        "target_groups_json": "TEXT DEFAULT '[]'",
        "platform": "VARCHAR(16) DEFAULT 'douyin'",
    }
    for name, ddl in task_extra.items():
        if name not in task_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE publishtask ADD COLUMN {name} {ddl}")
                )

    if "imageasset" in inspector.get_table_names():
        image_columns = {
            column["name"] for column in inspector.get_columns("imageasset")
        }
        image_extra = {
            "title": "VARCHAR(120) DEFAULT ''",
            "category": "VARCHAR(60) DEFAULT '未分类'",
        }
        for name, ddl in image_extra.items():
            if name not in image_columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE imageasset ADD COLUMN {name} {ddl}")
                    )

    if "postmetric" in inspector.get_table_names():
        metric_columns = {
            column["name"] for column in inspector.get_columns("postmetric")
        }
        if "body" not in metric_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE postmetric ADD COLUMN body TEXT DEFAULT ''")
                )

    if "consoletoken" in inspector.get_table_names():
        ct_columns = {
            column["name"] for column in inspector.get_columns("consoletoken")
        }
        ct_extra = {
            "frozen": "BOOLEAN DEFAULT 0",
            "expires_at": "DATETIME",
            "renewal_requested": "BOOLEAN DEFAULT 0",
        }
        for name, ddl in ct_extra.items():
            if name not in ct_columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE consoletoken ADD COLUMN {name} {ddl}")
                    )

    if "contentitem" in inspector.get_table_names():
        content_columns = {
            column["name"] for column in inspector.get_columns("contentitem")
        }
        if "cover_title" not in content_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE contentitem ADD COLUMN cover_title VARCHAR(200) DEFAULT ''")
                )
        if "city" not in content_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE contentitem ADD COLUMN city VARCHAR(40) DEFAULT '通用'")
                )
        if "platform" not in content_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE contentitem ADD COLUMN platform VARCHAR(16) DEFAULT 'douyin'")
                )
        for name, ddl in {"draft_id": "INTEGER", "prompt_version_id": "INTEGER"}.items():
            if name not in content_columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE contentitem ADD COLUMN {name} {ddl}")
                    )


    if "contentdraft" in inspector.get_table_names():
        draft_columns = {
            column["name"] for column in inspector.get_columns("contentdraft")
        }
        draft_extra = {
            "prompt_tokens": "INTEGER DEFAULT 0",
            "completion_tokens": "INTEGER DEFAULT 0",
            "cost_cny": "FLOAT DEFAULT 0",
            "dup_score": "FLOAT DEFAULT 0",
            "dup_of": "VARCHAR(200) DEFAULT ''",
            # 提示词溯源（见 PromptVersion 的注释）
            "prompt_version_id": "INTEGER",
            "original_body": "TEXT DEFAULT ''",
            "reject_reason": "VARCHAR(200) DEFAULT ''",
        }
        for name, ddl in draft_extra.items():
            if name not in draft_columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE contentdraft ADD COLUMN {name} {ddl}")
                    )

    if "deviceaccount" in inspector.get_table_names():
        acc_columns = {
            column["name"] for column in inspector.get_columns("deviceaccount")
        }
        if "logged_in" not in acc_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE deviceaccount ADD COLUMN logged_in BOOLEAN")
                )
        if "auto_broadcast" not in acc_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE deviceaccount "
                        "ADD COLUMN auto_broadcast BOOLEAN DEFAULT 0"
                    )
                )

    device_extra = {
        "douyin_nickname": "VARCHAR(120)",
        "douyin_id": "VARCHAR(64)",
        "auto_publish": "BOOLEAN DEFAULT 0",
        "daily_quota": "INTEGER DEFAULT 2",
        "health": "VARCHAR(16) DEFAULT 'normal'",
        "health_message": "VARCHAR(200)",
        "city": "VARCHAR(40) DEFAULT '未分组'",
        "metrics_refresh_requested": "BOOLEAN DEFAULT 0",
        "accessibility_ok": "BOOLEAN",
        "accessibility_since": "DATETIME",
        "account_refresh_requested": "BOOLEAN DEFAULT 0",
    }
    for name, ddl in device_extra.items():
        if name not in device_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE device ADD COLUMN {name} {ddl}")
                )

    # ⚠ **必须放在所有 ALTER 之后。** 放在中间的话，后面那些块才加的列，
    # 轮到建索引时还不存在，会被静默跳过 —— 我第一版就放在 contentitem 块里，
    # 在生产库副本上跑出来 contentdraft 的索引没建上。
    #
    # ⚠ **ALTER 加的列不会自动建索引。** SQLModel 的 `index=True` 只在
    # `create_all` 建表那一次生效；后来 ALTER 上去的列，模型里写了也没有。
    # 库里 contentitem 的 city / platform 就是这样 —— 标了 index=True，
    # 实际一个索引都没有。新加的溯源列要按 prompt_version_id 分组统计
    # （「哪版提示词写的内容表现更好」），没索引就是全表扫。
    _ensure_indexes(
        ("ix_contentdraft_prompt_version_id", "contentdraft", "prompt_version_id"),
        ("ix_contentitem_draft_id", "contentitem", "draft_id"),
        ("ix_contentitem_prompt_version_id", "contentitem", "prompt_version_id"),
        ("ix_contentitem_city", "contentitem", "city"),
        ("ix_contentitem_platform", "contentitem", "platform"),
    )

    _backfill_device_accounts()


def _backfill_device_accounts() -> None:
    """One-time: every existing Device gets a `douyin` DeviceAccount carrying its
    legacy account fields, so the new per-account model has data to work with.
    Idempotent — skips devices that already have a douyin account."""
    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text("SELECT device_id FROM deviceaccount WHERE platform='douyin'")
            )
        }
        rows = conn.execute(
            text(
                "SELECT id, douyin_nickname, douyin_id, city, auto_publish, "
                "daily_quota, health, health_message FROM device"
            )
        ).fetchall()
        for r in rows:
            if r[0] in existing:
                continue
            conn.execute(
                text(
                    "INSERT INTO deviceaccount (device_id, platform, nickname, "
                    "account_id, city, auto_publish, auto_broadcast, "
                    "daily_quota, health, "
                    "health_message, created_at, updated_at) VALUES "
                    "(:d, 'douyin', :nick, :aid, :city, :ap, 0, :dq, :h, :hm, "
                    ":now, :now)"
                ),
                {
                    "d": r[0], "nick": r[1], "aid": r[2],
                    "city": r[3] or "未分组", "ap": r[4] or 0,
                    "dq": r[5] or 2, "h": r[6] or "normal", "hm": r[7],
                    "now": datetime.utcnow().isoformat(),
                },
            )

        # 把设备上那份 health 影子副本和抖音账号那份对齐（账号是真相）。
        # 历史遗留：心跳同时写两份，而「标记正常」的按钮只写账号那一份，
        # 于是 device.health 一旦置成 abnormal 就再也回不去 —— 线上因此挂着
        # 7 条永远清不掉的「设备异常」告警，而设备页显示全绿
        # （serialize_device 用账号的 health 覆盖了同名字段，看不出分叉）。
        # 写入侧已经修好（devices.py set_health 现在两份一起写），这里只补上
        # 存量数据。幂等：对齐之后每次启动都是零更新。
        conn.execute(
            text(
                "UPDATE device SET health = ("
                "  SELECT a.health FROM deviceaccount a"
                "  WHERE a.device_id = device.id AND a.platform = 'douyin'"
                "), health_message = ("
                "  SELECT a.health_message FROM deviceaccount a"
                "  WHERE a.device_id = device.id AND a.platform = 'douyin'"
                ") WHERE EXISTS ("
                "  SELECT 1 FROM deviceaccount a"
                "  WHERE a.device_id = device.id AND a.platform = 'douyin'"
                "    AND IFNULL(a.health, 'normal') <> IFNULL(device.health, 'normal')"
                ")"
            )
        )


def get_session():
    with Session(engine) as session:
        yield session



def _ensure_indexes(*specs: tuple[str, str, str]) -> None:
    """幂等地补索引。`CREATE INDEX IF NOT EXISTS` SQLite 原生支持。

    只对已存在的表动手 —— 全新的库由 `create_all` 建索引，这里是给
    「先建表、后 ALTER 加列」的老库补课。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for index_name, table_name, column in specs:
        if table_name not in existing_tables:
            continue
        if column not in {c["name"] for c in inspector.get_columns(table_name)}:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} ({column})"
                )
            )
