"""群消息文案的自动检查。

用户的要求是：**「所有的通知要和目前成熟 App 和产品的通知描述一样，不是项目说明和
产品说明，不要出现此类的语气和词汇，类似口径、回采等。」**

文案这种东西，改一次容易，守住难 —— 下次加个功能顺手写一句「本次回采未命中基线」
就又滑回去了，而且不会有任何报错。所以这里把它变成一条会红的用例：
从 AST 里抠出所有**会进消息**的中文串（跳过 docstring 和注释），拿禁用词表撞一遍。

新增文案时如果被这条用例拦住，正确做法是换个说法，而不是往白名单里加词。
"""

import ast
import io
import re
from pathlib import Path

CJK = re.compile(r"[一-鿿]")

# 会把字符串发到群里的模块
SOURCES = [
    "app/services/notify.py",
    "app/services/notify_sweep.py",
    "app/services/tasks.py",
    "app/services/auto_broadcast.py",
    "app/services/content_supply.py",
]

# 行话：内部机制、统计术语、项目黑话。出现即说明又在跟运营讲系统内部了。
JARGON = [
    # —— 统计与产品术语 ——
    "口径", "分母", "分子", "底数", "覆盖度", "增量", "环比", "同比",
    "产能", "达成率", "水位", "存量", "采样", "聚合", "阈值", "颗粒度",
    # —— 项目黑话 ——
    # 「消耗」「入库」「掉队」故意**不**禁：它们是仓库和日常中文里就有的词，
    # 运营看得懂。真正该改的是「出比进多」「掉队的号」这种搭配，不是词本身。
    # 「一轮」同理只禁搭配，不禁裸字「轮」，否则「第二轮补稿」也会被误伤。
    "回采", "通用池", "内容池", "专属", "挂零", "转人工", "二改", "矩阵",
    "顶掉", "查重", "出比进", "进比出", "不并进",
    "空白不等于", "抢完就没", "轮都不够", "够 0", "一轮都不",
    # —— 内部机制 ——
    "心跳", "续租", "租约", "无障碍", "未就绪", "掉了绑定", "释放设备",
    "入队", "派发", "去重", "兜底", "幂等", "状态机", "埋点", "链路",
    "终态", "回落", "字段", "枚举", "接口", "进程", "线程", "队列",
    "排队中", "下发", "拉取", "调度", "实例", "灰度", "归因", "回填",
    # —— 自我说明 / 需求文档腔 ——
    "该功能", "本功能", "本模块", "详见", "如上所述", "综上", "请知悉",
    "赋能", "抓手", "闭环", "打通", "沉淀",
    # 英文字段名 / 技术标识，一个都不许漏进消息
    "reason", "detail", "payload", "task_id", "device_id", "account_id",
    "errcode", "traceback", "dedup", "None", "null",
]

# 看着像行话、但确实是业务里天天在说的词，必须保留
ALLOWED = ["发布", "账号", "城市", "内容", "设备", "任务", "群发", "作品"]


def _message_strings(path: Path) -> list[tuple[int, str]]:
    """所有会进消息的中文字符串字面量。

    跳过三类：docstring、注释（本来就不是字面量）、以及**日志**——
    日志是写给我们自己看的，行话在那里是准确而不是傲慢。
    """
    source = io.open(path, encoding="utf-8").read()
    tree = ast.parse(source)
    # 名字里带 LEGACY 的字典，它的**键**是历史数据里存着的旧措辞，用来做匹配的，
    # 不是我们要发出去的话（值才是）。这类键必须豁免，否则「翻译旧文案」这件事
    # 本身会被这条检查拦住。
    legacy_keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any("LEGACY" in n for n in names):
            continue
        if isinstance(node.value, ast.Dict):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    legacy_keys.add(key.value)
    logged = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name in {"debug", "info", "warning", "error", "exception", "critical"}:
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    logged.add(arg.value)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value in docstrings or value in logged or value in legacy_keys:
                continue
            if not CJK.search(value):
                continue
            found.append((getattr(node, "lineno", 0), value))
    return found


def test_群消息里没有行话():
    """会发到群里的中文串不许命中行话表。"""
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for rel in SOURCES:
        path = root / rel
        for lineno, text in _message_strings(path):
            for word in JARGON:
                if word in text:
                    offenders.append(f"{rel}:{lineno}  「{word}」→ {text!r}")
    assert not offenders, (
        "群消息里出现了内部行话（运营看不懂，也不该看到）。\n"
        "正确做法是换个说法，不是把词加进白名单。\n  " + "\n  ".join(offenders)
    )


def test_行话表本身没有误伤正常业务词():
    """防止这条检查被写死到没法表达业务。"""
    for word in ALLOWED:
        # 单向判断：行话词是不是会命中这个业务词。反过来不算 ——
        # 禁掉「内容池」不等于禁掉「内容」。
        assert not any(j in word for j in JARGON), (
            f"「{word}」是业务里天天在说的词，不该被行话表命中"
        )
