# -*- coding: utf-8 -*-
"""重排 @media 之前必须先问的两个问题。

① **现在哪些规则是被后面的 @media 白白盖掉的？**（乱序的直接后果）
   两个 @media 的 max-width 一大一小，如果大的写在小的后面，
   在小屏那一档大的也匹配、而且后写，于是小屏那一档的规则永远不生效。

② **把 @media 全部挪到文件末尾，会不会翻转某些优先级？**
   只有当某个选择器**既出现在某个 @media 里、又出现在它后面的基础规则里**时，
   现在是基础规则赢；挪到末尾后变成 @media 赢。这类必须逐个看，不能机械搬。
"""
import io
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
text = io.open(sys.argv[1], encoding="utf-8").read()
text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)

# 切出顶层 @media 块（带完整花括号配对）
blocks = []          # (start, end, condition, [selectors])
i = 0
while True:
    m = re.compile(r"@media([^{]*)\{").search(text, i)
    if not m:
        break
    depth, j = 1, m.end()
    while depth and j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    body = text[m.end(): j - 1]
    sels = set()
    for r in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        for s in r.group(1).split(","):
            s = " ".join(s.split())
            if s:
                sels.add(s)
    blocks.append((m.start(), j, m.group(1).strip(), sels))
    i = j

def width(cond):
    w = re.search(r"max-width:\s*(\d+)", cond)
    return int(w.group(1)) if w else None

print("=== ① 被后面的宽断点白白盖掉的规则 ===")
dead = 0
for a in range(len(blocks)):
    wa = width(blocks[a][2])
    if wa is None:
        continue
    for b in range(a + 1, len(blocks)):
        wb = width(blocks[b][2])
        if wb is None or wb <= wa:
            continue          # 后面那个更窄 → 正常，本来就该它赢
        overlap = blocks[a][3] & blocks[b][3]
        if overlap:
            print(f"  @{wa} 的规则被后写的 @{wb} 盖住（{wa}px 以下两个都匹配）：")
            for s in sorted(overlap):
                print(f"      {s}")
            dead += len(overlap)
print(f"  合计 {dead} 条选择器在窄档上永远不生效\n")

print("=== ② 挪到末尾会翻转优先级的（@media 里的选择器又出现在它后面的基础规则里）===")
flip = 0
for start, end, cond, sels in blocks:
    tail = text[end:]
    # 去掉 tail 里的 @media 块，只看基础规则
    tail_base = re.sub(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", tail, flags=re.S)
    for r in re.finditer(r"([^{}]+)\{([^{}]*)\}", tail_base):
        for s in r.group(1).split(","):
            s = " ".join(s.split())
            if s and s in sels:
                print(f"  @{cond.strip()} 里的 `{s}` 之后又被基础规则重写")
                flip += 1
print(f"  合计 {flip} 处" + ("  → 可以安全重排" if not flip else "  → 这些要单独看"))
