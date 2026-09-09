# -*- coding: utf-8 -*-
"""把 styles.css 里所有 var() 回代成字面值，每条声明一行输出。

**为什么需要它**：令牌化的每一步都声称「只是改名，页面不动」。这句话没法靠
肉眼比 33 张截图证明 —— 一个漏网的 `padding: 10px` 变成 `var(--sp-4)`(8px)
只差 2px，截图上看不出来，几周后才有人说「这里怎么歪了」。
回代之后两份文件逐行 diff，差异有几行、是哪几行，是**机械可证**的。

用法：
    python tools/resolve_css.py <css 文件> > out.txt
    # 基线：git show HEAD:frontend/src/styles.css > /tmp/base.css

输出规范化到「一条声明一行、去掉自定义属性声明本身」，所以新增令牌不会
产生差异行；只有真正被解析成不同字面值的声明才会出现在 diff 里。
"""
import re
import sys

VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)")
CUSTOM_PROP = re.compile(r"^\s*--[\w-]+\s*:")


def collect_tokens(text: str) -> dict:
    """只收**顶层第一个 `:root` 块**里的自定义属性。

    ⚠ 以前是全文件扫、后写的覆盖先写的 —— 一旦断点里出现
    `@media (max-width: 820px) { :root { --topbar-h: 66px } }`，
    基础层的规则也会被按 66px 回代，宽屏的比对结果就全错了
    （表现为「min-height 从 94px 变成 66px」这种根本没发生的差异）。
    基础层只认基础层的值。"""
    m = re.search(r":root\s*\{(.*?)\n\}", text, re.S)
    body = m.group(1) if m else ""
    return {
        d.group(1): d.group(2).strip()
        for d in re.finditer(r"(--[\w-]+)\s*:\s*([^;{}]+);", body)
    }


def expand(value: str, tokens: dict, depth: int = 0) -> str:
    if depth > 12:
        return value
    def sub(m):
        name, fallback = m.group(1), m.group(2)
        if name in tokens:
            return expand(tokens[name], tokens, depth + 1)
        if fallback is not None:
            return expand(fallback.strip(), tokens, depth + 1)
        return m.group(0)
    out = VAR.sub(sub, value)
    return expand(out, tokens, depth + 1) if VAR.search(out) and out != value else out


def main(path: str) -> None:
    text = open(path, encoding="utf-8").read()
    tokens = collect_tokens(text)
    # 注释整段去掉：令牌层带来大量注释，它们不是渲染结果
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for chunk in text.split("}"):
        sel, _, body = chunk.rpartition("{")
        if not sel:
            continue
        selector = " ".join(sel.split())
        for decl in body.split(";"):
            decl = " ".join(decl.split())
            if not decl or CUSTOM_PROP.match(decl + ":"):
                continue
            if decl.lstrip().startswith("--"):
                continue          # 自定义属性声明本身不算渲染结果
            out.append(f"{selector} {{ {expand(decl, tokens)} }}")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
