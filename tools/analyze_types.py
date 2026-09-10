# -*- coding: utf-8 -*-
"""按内容风格算效果 —— note 级、块内配对、带置信区间。

**这个脚本存在的理由**：闭环方案里所有「A 档比 B 档好 0.08 个位次」这类
数字，此前都是仓库外一次性算出来的，谁都无法重跑。一个无法重跑的统计结论
和没有结论是一回事 —— 有人质疑时拿不出过程，过两周自己也想不起口径。

**三条口径，错一条结论就不成立：**

1. **note 级，不是行级。** `PostMetric` 是变化事件流（四个计数全等就不写行），
   实测 3.37 行/帖、单帖最多 21 行。按行统计等于给活跃的帖子加权，
   有效样本被高估两三倍，置信区间假性变窄。
2. **块内比较。** 账号本身的权重差异和内容类型的差异同量级，直接跨账号比
   等于在噪声里找信号。块 = (设备, ISO 周)，只比同一块内部的相对位次。
3. **先滤噪声。** 抖音官方推广内容（浏览 0、评论上千）会主宰以评论为主指标
   的一切排序。见 `content_types.is_noise`。

用法：
    python tools/analyze_types.py <agent.db 的路径>

⚠ 拷贝生产库做分析时，**必须连 `-wal` `-shm` 一起拷**，只拷 .db 会拿到
撕裂的快照（报 malformed database schema）。
"""
import collections
import datetime as dt
import math
import random
import sqlite3
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.content_types import STYLES, STYLE_OF, TYPES, classify, is_noise, style

BOOTSTRAP = 5000
SEED = 20260910


def load_notes(db: str) -> list[dict]:
    """取 note 级最新快照。一帖一行。"""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT * FROM postmetric
        WHERE id IN (SELECT MAX(id) FROM postmetric
                     GROUP BY device_id, platform, title)
          AND platform = 'douyin'
        """
    ).fetchall()
    first_seen = {
        (r["device_id"], r["platform"], r["title"]): r["f"]
        for r in con.execute(
            """SELECT device_id, platform, title, MIN(captured_at) f
               FROM postmetric GROUP BY device_id, platform, title"""
        )
    }
    con.close()
    notes, dropped = [], collections.Counter()
    for r in rows:
        why = is_noise(r["title"] or "", r["views"] or 0)
        if why:
            dropped[why] += 1
            continue
        notes.append(
            {
                "device_id": r["device_id"],
                "title": r["title"] or "",
                "views": r["views"] or 0,
                "comments": r["comments"] or 0,
                "likes": r["likes"] or 0,
                "content_id": r["content_id"],
                "first_seen": (first_seen.get(
                    (r["device_id"], r["platform"], r["title"])) or "")[:10],
                "type": classify(r["title"] or ""),
                "style": style(r["title"] or ""),
            }
        )
    print(f"取到 {len(rows)} 个 note，排除 {sum(dropped.values())}：{dict(dropped)}")
    return notes


def drop_truncated(notes: list[dict]) -> list[dict]:
    """同一设备下互为前缀的标题只留最长的那条。

    回采时标题会被平台截断，同一个帖子会以「完整版」和「截断版」两条身份
    进来（实测 15 条）。不合并的话，一个爆款会被算成两个爆款。
    """
    by_dev = collections.defaultdict(list)
    for n in notes:
        by_dev[n["device_id"]].append(n)
    keep = []
    for _dev, items in by_dev.items():
        items.sort(key=lambda x: -len(x["title"]))
        seen: list[str] = []
        for n in items:
            head = n["title"][:20]
            if any(s.startswith(head) for s in seen):
                continue
            seen.append(n["title"])
            keep.append(n)
    if len(keep) != len(notes):
        print(f"合并截断重复：{len(notes)} → {len(keep)}")
    return keep


def blocks(notes: list[dict]) -> dict[tuple, list[dict]]:
    """块 = (设备, ISO 周)。同块内部才可比。"""
    out = collections.defaultdict(list)
    for n in notes:
        if not n["first_seen"]:
            continue
        y, w, _ = dt.date.fromisoformat(n["first_seen"]).isocalendar()
        out[(n["device_id"], y, w)].append(n)
    return out


def within_block_rank(notes: list[dict], key: str) -> list[dict]:
    """给每条算一个块内位次（0~1，越大越好）。

    用位次不用原值：评论数在块之间差一两个数量级，直接平均会被大块带走。
    位次是块内的相对好坏，账号权重和当周大盘自动被消掉。
    """
    scored = []
    for _b, items in blocks(notes).items():
        if len(items) < 2:      # 一条的块给不出位次
            continue
        ordered = sorted(items, key=lambda x: x[key])
        n = len(ordered)
        for i, item in enumerate(ordered):
            # 并列取平均位次
            same = [j for j, o in enumerate(ordered) if o[key] == item[key]]
            item = dict(item)
            item["rank"] = (sum(same) / len(same)) / (n - 1)
            scored.append(item)
    return scored


def boot_ci(values: list[float], reps: int = BOOTSTRAP) -> tuple[float, float]:
    rng = random.Random(SEED)
    n = len(values)
    if n < 2:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(reps):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(reps * 0.025)], means[int(reps * 0.975)]


def report(scored: list[dict], key_name: str, metric: str) -> dict[str, tuple]:
    groups = collections.defaultdict(list)
    for s in scored:
        groups[s[key_name]].append(s["rank"])
    print(f"\n=== 按{'风格' if key_name == 'style' else '类型'}的块内位次（主指标 {metric}）===")
    print(f"{'':14}{'n':>6}{'位次':>8}{'95% CI':>22}{f'{metric}中位':>10}")
    out = {}
    raw = collections.defaultdict(list)
    for s in scored:
        raw[s[key_name]].append(s[metric])
    for name, ranks in sorted(groups.items(), key=lambda kv: -st.mean(kv[1])):
        lo, hi = boot_ci(ranks)
        out[name] = (len(ranks), st.mean(ranks), lo, hi)
        print(f"  {name:12}{len(ranks):>6}{st.mean(ranks):>8.3f}"
              f"   [{lo:.3f}, {hi:.3f}]{st.median(raw[name]):>10.0f}")
    return out


def gap_ci(scored: list[dict], key_name: str, a: str, b: str) -> None:
    """两组位次差的置信区间 —— 判据就看它含不含 0。"""
    ga = [s["rank"] for s in scored if s[key_name] == a]
    gb = [s["rank"] for s in scored if s[key_name] == b]
    if len(ga) < 2 or len(gb) < 2:
        print(f"  {a} vs {b}: 样本不够")
        return
    rng = random.Random(SEED)
    diffs = []
    for _ in range(BOOTSTRAP):
        ma = sum(ga[rng.randrange(len(ga))] for _ in range(len(ga))) / len(ga)
        mb = sum(gb[rng.randrange(len(gb))] for _ in range(len(gb))) / len(gb)
        diffs.append(ma - mb)
    diffs.sort()
    lo, hi = diffs[int(BOOTSTRAP * 0.025)], diffs[int(BOOTSTRAP * 0.975)]
    verdict = "✅ 不含 0，差异站得住" if lo * hi > 0 else "❌ 含 0，分不开"
    print(f"  {a} vs {b}: 差 {st.mean(ga) - st.mean(gb):+.3f}  "
          f"CI [{lo:+.3f}, {hi:+.3f}]  {verdict}")


def main(db: str) -> None:
    notes = drop_truncated(load_notes(db))
    ours = [n for n in notes if n["content_id"]]
    print(f"\n可用 note {len(notes)}，其中对得上内容库的（我们发的）{len(ours)}")

    print("\n=== 类型分布 ===")
    dist = collections.Counter(n["type"] for n in notes)
    for name, _d, _p in TYPES:
        n = dist.get(name, 0)
        print(f"  {name:12}{n:>5}  {'█' * (n * 40 // max(dist.values()))}")

    scored = within_block_rank(notes, "comments")
    print(f"\n进入块内比较的 note：{len(scored)}（块 ≥2 条才可比）")
    report(scored, "type", "comments")
    report(scored, "style", "comments")

    print("\n=== 三档两两能不能分开（判据：CI 不含 0）===")
    for i, a in enumerate(STYLES):
        for b in STYLES[i + 1:]:
            gap_ci(scored, "style", a, b)

    # 分不开的两档要合并 —— 「分不开」不是「差不多」，是「这条线不该画」。
    print("\n=== 合并之后：二选一 vs 其余 ===")
    for x in scored:
        x["two"] = "二选一" if x["style"] == "二选一" else "其余"
    gap_ci(scored, "two", "二选一", "其余")
    for k in ("二选一", "其余"):
        c = sorted(x["comments"] for x in scored if x["two"] == k)
        print(f"  {k:5} n={len(c):5} 评论中位 {st.median(c):.0f}  p90 {c[int(len(c) * 0.9)]}")

    print("\n=== 折半复现（随机对半，两半排序稳不稳）===")
    rng = random.Random(SEED)
    half = scored[:]
    rng.shuffle(half)
    for name, part in (("前一半", half[:len(half) // 2]), ("后一半", half[len(half) // 2:])):
        g = collections.defaultdict(list)
        for x in part:
            g[x["style"]].append(x["rank"])
        order = sorted(g, key=lambda k: -st.mean(g[k]))
        print(f"  {name}：" + " > ".join(
            f"{k}({st.mean(g[k]):.3f},n={len(g[k])})" for k in order))

    print("\n=== 只看我们自己发的 ===")
    scored_ours = within_block_rank(ours, "comments")
    print(f"  进入比较的：{len(scored_ours)}")
    if scored_ours:
        report(scored_ours, "style", "comments")
        gap_ci(scored_ours, "style", "纠结对比", "日常讲述")
        gap_ci(scored_ours, "style", "日常讲述", "清单行情")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法：python tools/analyze_types.py <agent.db>")
    main(sys.argv[1])
