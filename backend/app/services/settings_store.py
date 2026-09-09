"""Generic runtime key/value settings (AppSetting) — used by the first-run wizard
(adb endpoints, firstrun flag) and anywhere else that needs a small persisted knob.
"""
from sqlalchemy import text
from sqlmodel import Session

from app.models.entities import AppSetting, utcnow


def get(session: Session, key: str, default: str = "") -> str:
    """读一个设置值。**读不出来一律退回默认值，绝不抛。**

    一行设置值不该有能力停掉整个后台 loop，但它有过：用远程 cmd 控制台
    （GBK）往这张表写中文，落进去的就是非 UTF-8 字节，之后 sqlite3 驱动在
    **fetch 阶段**就抛 OperationalError("Could not decode to UTF-8")。而
    notify_sweep.sweep() 每一段都 try/except 吞异常，于是表现是「通知毫无征兆地
    停了」—— 不报错、不留痕，2 小时播报和日报一起哑掉。
    写入侧要用 UTF-8（后端自己写是对的，人工改库请用 hex 字面量），
    读取侧则必须有这层兜底。
    """
    try:
        row = session.get(AppSetting, key)
    except Exception:  # noqa: BLE001 — 驱动层的解码错也要接住
        session.rollback()
        return default
    if row is None or row.value is None:
        return default
    # 值也可能不是 str（历史上被人当 BLOB 写进去过），别让它流到 .split() 那种地方
    return row.value if isinstance(row.value, str) else default


def put(session: Session, key: str, value: str) -> None:
    """写一个设置值。**坏掉的旧行不能挡住新值写进去。**

    put 也要先读一次，所以同样会踩到 get 那条注释里的解码错。真踩到的时候，
    ORM 已经没法 merge 那一行了 —— 只能先物理删掉再插，否则这个 key 会永远
    卡在坏值上、连修都修不回来。
    """
    try:
        row = session.get(AppSetting, key)
    except Exception:  # noqa: BLE001 — 驱动层解码错
        session.rollback()
        session.execute(text("DELETE FROM appsetting WHERE key = :k"), {"k": key})
        session.commit()
        row = None
    if row:
        row.value = value
        row.updated_at = utcnow()
    else:
        row = AppSetting(key=key, value=value)
    session.add(row)
    session.commit()
