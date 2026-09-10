# -*- coding: utf-8 -*-
"""内容风格分类器 —— **转发**。真正的实现在 `backend/app/services/content_taxonomy.py`。

为什么不在这里放一份拷贝：后端要按类型显示、按类型分配配额，分析脚本要按
类型算效果。两边各存一份，判据迟早分叉，而分叉的表现是
「效果榜上说这是二选一，配额却没按二选一发」—— 没人查得出来。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.content_taxonomy import (  # noqa: F401,E402
    OFFICIAL,
    STYLE_OF,
    STYLES,
    TYPES,
    classify,
    cover_of,
    is_noise,
    style,
)
