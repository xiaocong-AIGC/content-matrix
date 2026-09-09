"""补货的触发器：让「库存少了」自己去叫醒补货，而不是等下一次轮询。

**为什么不能只靠定时循环**（这版之前就是每小时一轮）：
运营在界面上把「深圳」的自动生成打开，然后盯着看 —— 界面上什么都不动，
最长干等一小时。人只会以为它坏了，转头去点「现在生成一批」，反而多花一次钱。
「打开开关」和「开始生成」之间不该有一个人看得见的空档。

所以水位驱动要真的由**水位事件**驱动：
- 打开一个城市的自动生成 → 立刻补（`force=True`，不受冷却约束）
- 一篇内容被发出去、真的出了池 → 叫醒一次，缺口够大就补

定时轮询保留，但退成**兜底**：有些消耗不经过发布链路（内容库里手工禁用/删除、
新增账号让 need 变大），没有事件可发，只能靠隔一阵子自己看一眼。

进程模型：后端是单进程单 worker，所以进程内的一个 asyncio.Event 就够了。
但 `poke()` 会被**同步接口**在线程池里调到（FastAPI 的 def 路由跑在 threadpool），
而 `Event.set()` 不是线程安全的 —— 必须走 `call_soon_threadsafe` 回到事件循环。
"""

from __future__ import annotations

import asyncio
import threading

# 两次事件驱动的补货之间至少隔这么久。一波任务同时结束会连打好几次 poke，
# 没有它就会贴着连跑。`force` 的 poke（运营手动开城市）不受这个约束。
COOLDOWN_SECONDS = 300
# 兜底轮询间隔。事件驱动接管了"及时"这件事之后，它只负责兜住没有事件的那些消耗。
SAFETY_NET_SECONDS = 1800

_loop: asyncio.AbstractEventLoop | None = None
_event: asyncio.Event | None = None
_lock = threading.Lock()
_reasons: list[str] = []
_forced = False


def bind(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
    """补货循环启动时把自己的事件登记进来。"""
    global _loop, _event
    _loop = loop
    _event = event


def poke(reason: str, *, force: bool = False) -> None:
    """告诉补货循环「该看一眼了」。任何线程都能调，不会抛。

    `force=True` = 跳过冷却立刻跑（只给"运营刚打开这个城市"这一种情况用）。
    """
    global _forced
    with _lock:
        if reason not in _reasons:
            _reasons.append(reason)
        if force:
            _forced = True
    loop, event = _loop, _event
    if loop is None or event is None:
        return  # 循环还没起来（比如测试里），下一轮兜底会看到
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        pass  # 循环已经关了，服务正在退出


def take() -> tuple[list[str], bool]:
    """取走攒下的理由，并清空。返回 (理由, 是否要求跳过冷却)。"""
    global _forced
    with _lock:
        reasons, forced = list(_reasons), _forced
        _reasons.clear()
        _forced = False
    return reasons, forced


def pending() -> bool:
    with _lock:
        return bool(_reasons)
