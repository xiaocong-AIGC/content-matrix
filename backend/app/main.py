import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router
from app.core.config import get_settings
from app.db.session import create_db_and_tables, engine
from sqlmodel import Session


async def _retention_loop():
    """Daily background purge of old finished tasks (bounds DB/disk growth)."""
    from app.services.tasks import purge_old_data

    days = get_settings().retention_days
    while True:
        try:
            with Session(engine) as session:
                purge_old_data(session, days)
        except Exception:  # noqa: BLE001 — never let cleanup crash the app
            pass
        await asyncio.sleep(6 * 3600)


async def _confirmation_sweep_loop():
    """Every minute, auto-fail tasks stuck in waiting_confirmation past the
    timeout so a stuck page never blocks the phone's queue (incl. 群发) forever."""
    from app.services.tasks import fail_stalled_tasks, fail_stuck_confirmations

    timeout = get_settings().confirmation_timeout_seconds
    while True:
        try:
            with Session(engine) as session:
                fail_stuck_confirmations(session, timeout)
                # 卡在 running 里不动的任务：租约在续所以回收管不到，
                # 又不在 waiting_confirmation 所以上面那条也管不到。
                fail_stalled_tasks(session)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(60)


async def _notify_dispatch_loop():
    """每 15 秒把待发告警发出去。

    发送**只在这里**做——业务代码只 enqueue 一行 pending 就返回，绝不在请求线程里
    发 HTTP（外发一慢就会拖住 Agent 上报状态，进而卡住整台设备）。
    """
    from app.services.notify import dispatch_pending

    def _round():
        with Session(engine) as session:
            dispatch_pending(session)

    while True:
        try:
            # ⚠ 这一行以前是直接在协程里跑的，而它就是那个发 HTTP 的地方 ——
            # 上面这段注释说的「绝不在请求线程里发」做到了，却漏了更糟的一种：
            # 在**事件循环**里发。企微那边一慢（默认超时 5s，每 15 秒一轮），
            # 整个服务连同所有 Agent 上报一起被按住。
            await asyncio.to_thread(_round)
        except Exception:  # noqa: BLE001 — 通知挂了也不能影响主服务
            pass
        await asyncio.sleep(15)


async def _auto_broadcast_loop():
    """每 2 分钟把「到点该推的群消息」排进任务表。

    只补已经到点的时刻，且排程本身是幂等的（时刻可复现 + 按设备和时刻查重），
    所以反复跑、进程重启都不会重复排。间隔取 2 分钟是为了让实际发出时间和
    设定时刻的偏差控制在几分钟内 —— 再密没有意义，Agent 还要排队领任务。
    """
    from app.services.auto_broadcast import plan_all

    await asyncio.sleep(45)
    while True:
        try:
            with Session(engine) as session:
                plan_all(session)
        except Exception:  # noqa: BLE001 — 排程挂了不能带走整个服务
            pass
        await asyncio.sleep(120)


async def _content_supply_loop():
    """内容自动补货：**水位事件驱动**，定时只当兜底。

    不是每天无脑生成 —— 那样内容会堆到发不完还白花钱。也不该是纯定时：
    运营打开「深圳」的自动生成之后盯着屏幕，如果要等下一次轮询才动，
    这中间的空档在人看来就是"坏了"，他会转头去点「现在生成一批」，反而多花一次钱。

    所以真正的触发点是水位本身（见 services/supply_trigger.py）：
    打开一个城市 → 立刻补；一篇内容发出去、真的出了池 → 叫醒一次。
    定时那一轮留着，是因为有些消耗没有事件可发（内容库里手工禁用/删除、
    新增账号让 need 变大），只能靠隔一阵子自己看一眼。

    默认关着（supply:cities 为空）—— 这会真的花钱、真的往内容库里写东西。
    """
    from app.services import supply_trigger
    from app.services.content_supply import notify_result, run_once

    wake = asyncio.Event()
    supply_trigger.bind(asyncio.get_running_loop(), wake)

    await asyncio.sleep(90)
    last_run = 0.0
    while True:
        _, forced = supply_trigger.take()
        if not forced:
            # 冷却：一波任务同时结束会连打好几次 poke，不能贴着连跑。
            # 运营手动开城市那一次是 forced，不受这里约束。
            left = supply_trigger.COOLDOWN_SECONDS - (
                asyncio.get_running_loop().time() - last_run
            )
            if left > 0:
                await asyncio.sleep(left)
                supply_trigger.take()  # 冷却期间攒的并进这一轮，别再多跑一次
        wake.clear()
        last_run = asyncio.get_running_loop().time()
        try:
            # ⚠ 必须扔到线程里。`run_once` 里那一步是**同步调模型**，一轮几十秒，
            # 直接 await 不到、就这么写在协程里的话，整个事件循环被它按住 ——
            # 表现是补货期间控制台所有请求一起超时（改成事件驱动之后更要命：
            # 运营点完「保存」立刻触发生成，卡死的正好是他在看的那个页面）。
            def _supply_round():
                with Session(engine) as session:
                    result = run_once(session)
                # 通知必须在补货那一轮的事务之外发（写在 commit 之前会被写锁吃掉）
                with Session(engine) as session:
                    notify_result(session, result)

            await asyncio.to_thread(_supply_round)
        except Exception:  # noqa: BLE001 — 补货挂了不能带走整个服务
            pass
        try:
            await asyncio.wait_for(
                wake.wait(), timeout=supply_trigger.SAFETY_NET_SECONDS
            )
        except asyncio.TimeoutError:
            pass


async def _notify_sweep_loop():
    """每 5 分钟扫一次系统状态：掉线/未就绪/掉登录/账号异常/内容水位，
    到点还会推「今日进度播报」（每 2 小时）和每日 09:00 日报。

    企微群是运营的**主界面**（他们的原话：没异常就不去看控制台），所以正常
    节奏也要推，否则群里安静时分不清「一切正常」和「系统死了」。
    """
    from app.services.notify_sweep import sweep

    await asyncio.sleep(30)  # 让设备先完成一轮心跳，避免启动瞬间误报掉线
    def _round():
        with Session(engine) as session:
            sweep(session)

    while True:
        try:
            # 同上：sweep 里要发企微 webhook（网络 I/O，可能一直卡到超时），
            # 在事件循环里同步跑会把整个服务一起拖住。
            await asyncio.to_thread(_round)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    get_settings().storage_dir.mkdir(parents=True, exist_ok=True)
    loops = [
        asyncio.create_task(_retention_loop()),
        asyncio.create_task(_confirmation_sweep_loop()),
        asyncio.create_task(_notify_dispatch_loop()),
        asyncio.create_task(_notify_sweep_loop()),
        asyncio.create_task(_auto_broadcast_loop()),
        asyncio.create_task(_content_supply_loop()),
    ]
    yield
    for loop in loops:
        loop.cancel()


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
# FRONTEND_ORIGIN may be a comma-separated list, or "*" for internal/LAN
# deployment where the console is reached via the server's IP.
_origins = [o.strip() for o in settings.frontend_origin.split(",") if o.strip()]
# 桌面版(Tauri) webview 的固定来源始终放行——迁移到新电脑时如果 .env 没被加载，
# frontend_origin 会退回默认值(不含 tauri)，导致 webview 请求被 CORS 拦掉，表现为
# “连不上后端”。这些来源就是本机桌面壳自身，无条件放行是安全的。
for _o in ("tauri://localhost", "https://tauri.localhost", "http://tauri.localhost"):
    if _o not in _origins:
        _origins.append(_o)
_allow_all = "*" in _origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _origins,
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

# Serve the built console (frontend/dist) so a single exposed port gives the full
# UI — the basis of the server model (owner exposes :8010 via a tunnel; customers
# use a browser + a time-limited token; nothing is shipped to them). When frozen
# by PyInstaller (--add-data dist) the files live under sys._MEIPASS, not the repo.
import sys as _sys

if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
    _dist = Path(_sys._MEIPASS) / "frontend" / "dist"
else:
    # 源码运行：优先 dist-web（浏览器版，API 走相对路径）。dist 是桌面版产物，
    # 里面写死了 http://127.0.0.1:8010，被浏览器加载会连到访问者自己的电脑。
    _root = Path(__file__).resolve().parents[2] / "frontend"
    _dist = _root / "dist-web"
    if not _dist.is_dir():
        _dist = _root / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="console")

