"""「每天 N 次，时间落在指定时间段内随机」——发布和群发共用这一套。

用户定的形态：「每天自动推送次数可以设置，然后时间可以在规定的时间内随机，
比如我选时间为：10-11，14-15；那么每天两次推送都在这两个时间段里面随机就行，
发布也是。」

两个不显然但很重要的设计点：

**① 随机必须是可复现的。** 排程器跑在 5 分钟一轮的后台 loop 里，还会遇到进程重启。
如果每次算出来的时刻都不一样，同一天同一个号就会被反复排上不同的时间点。
所以随机数种子取 `(账号, 日期, 第几次)` ——**同一个号同一天的第 2 次，永远是同一个
时刻**。这样"算一遍"和"算一百遍"结果相同，幂等性不需要额外的状态表来保证。

**② 同城不同号要错开。** 种子里带账号 id，所以 8 个上海号即使配了同一个时间段，
落点也各不相同。22 个号在同一分钟给群里发广告是很明显的机器行为 ——
随机化在这里不只是产品需求。
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))

# 配置不合法时用它兜底，而不是让排程器崩掉（一个填错的设置值不该停掉发布）
DEFAULT_WINDOWS = "10:00-11:00,14:00-15:00"


def parse_windows(raw: str) -> list[tuple[int, int]]:
    """`"10:00-11:00,14:00-15:00"` → `[(600, 660), (840, 900)]`（当天第几分钟）。

    也接受 `10-11` 这种省略分钟的写法（用户就是这么说的）。
    任何一段解析不了就整段跳过，**不抛异常**：这个值是运营在前端填的，
    填错了应该是"这一段不生效"，而不是"发布全停"。
    """
    windows: list[tuple[int, int]] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if "-" not in chunk:
            continue
        head, _, tail = chunk.partition("-")
        start, end = _minutes(head), _minutes(tail)
        if start is None or end is None or end <= start:
            continue
        windows.append((start, end))
    return sorted(windows)


def _minutes(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    hour, _, minute = text.partition(":")
    try:
        h = int(hour)
        m = int(minute) if minute else 0
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def plan_times(
    *, windows: list[tuple[int, int]], count: int, day: datetime, key: str
) -> list[datetime]:
    """算出某一天要执行的 N 个时刻（中国时区的 aware datetime）。

    次数和时间段的个数不必相等：按顺序轮流分配（第 i 次落在第 `i % 段数` 段）。
    用户举的例子是 2 次 2 段，正好一段一次。

    `key` 用来定种子，传账号相关的东西（如 `f"publish:{account_id}"`）——
    同一个 key 同一天算出来的结果永远一样，见模块开头那段说明。
    """
    if count <= 0 or not windows:
        return []
    base = day.astimezone(CN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    times = []
    for index in range(count):
        start, end = windows[index % len(windows)]
        seed = f"{key}|{base:%Y%m%d}|{index}"
        rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
        minute = rng.randrange(start, end)      # 段末不含，落在 [start, end)
        second = rng.randrange(0, 60)           # 秒也散开，别让整点扎堆
        times.append(base + timedelta(minutes=minute, seconds=second))
    return sorted(times)


def due_times(
    *, windows: list[tuple[int, int]], count: int, now: datetime, key: str
) -> list[datetime]:
    """今天到现在为止**已经该发**、但还没排的时刻。

    排程器只补过去的点：未来的点等到了再说 —— 这样运营改了设置能立刻生效，
    而不是被今天早上算好的一张表锁死。
    """
    cn_now = now.astimezone(CN_TZ)
    planned = plan_times(windows=windows, count=count, day=cn_now, key=key)
    return [t for t in planned if t <= cn_now]


def describe(windows: list[tuple[int, int]]) -> str:
    """给消息和界面看的说法：`10:00-11:00、14:00-15:00`。"""
    return "、".join(
        f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}" for s, e in windows
    )


def to_utc_naive(moment: datetime) -> datetime:
    """中国时区 aware → 库里存的那种 naive UTC。"""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


__all__ = [
    "CN_TZ",
    "DEFAULT_WINDOWS",
    "describe",
    "due_times",
    "parse_windows",
    "plan_times",
    "to_utc_naive",
]

