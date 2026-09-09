"""「每天 N 次、时间段内随机」这套调度的性质。

两条是关键：随机必须可复现（否则 5 分钟一轮的 loop 会把同一个号反复排到不同时刻），
同城不同号必须错开（22 个号在同一分钟给群里发广告是很明显的机器行为）。
"""

from datetime import datetime, timedelta

from app.services.schedule_windows import (
    CN_TZ,
    describe,
    due_times,
    parse_windows,
    plan_times,
)


def test_解析用户会怎么填():
    # 用户原话就是「10-11，14-15」这种省略分钟的写法
    assert parse_windows("10-11,14-15") == [(600, 660), (840, 900)]
    assert parse_windows("10:00-11:00, 14:30-15:45") == [(600, 660), (870, 945)]
    # 填错的段直接跳过，不能抛 —— 一个填错的设置值不该把发布全停了
    assert parse_windows("乱写,25:00-26:00,11-10,,10-11") == [(600, 660)]
    assert parse_windows("") == []
    assert parse_windows(None) == []


def test_同一个号同一天算多少遍都是同一批时刻():
    """排程器跑在 5 分钟一轮的 loop 里，还会遇到进程重启。

    如果每次算出来的时刻都不一样，同一天同一个号就会被反复排上不同的时间点 ——
    幂等性就得靠额外的状态表来救。种子固定就不需要那张表。
    """
    windows = parse_windows("10-11,14-15")
    day = datetime(2026, 9, 4, 9, 0, tzinfo=CN_TZ)
    first = plan_times(windows=windows, count=2, day=day, key="publish:7")
    for _ in range(5):
        assert plan_times(windows=windows, count=2, day=day, key="publish:7") == first
    # 换一天要换一批
    tomorrow = day + timedelta(days=1)
    assert plan_times(windows=windows, count=2, day=tomorrow, key="publish:7") != first


def test_不同账号会错开():
    """同城 8 个号配同一个时间段，落点必须各不相同。"""
    windows = parse_windows("10-11")
    day = datetime(2026, 9, 4, 9, 0, tzinfo=CN_TZ)
    picks = {
        plan_times(windows=windows, count=1, day=day, key=f"publish:{i}")[0]
        for i in range(8)
    }
    assert len(picks) >= 7, f"8 个号挤在了 {len(picks)} 个时刻上，看着就是机器"


def test_时刻真的落在选定的段里():
    windows = parse_windows("10-11,14-15")
    day = datetime(2026, 9, 4, tzinfo=CN_TZ)
    for i in range(40):
        for moment in plan_times(windows=windows, count=4, day=day, key=f"k{i}"):
            minute = moment.hour * 60 + moment.minute
            assert (600 <= minute < 660) or (840 <= minute < 900), moment


def test_次数和段数不相等时轮流分配():
    windows = parse_windows("10-11,14-15")
    day = datetime(2026, 9, 4, tzinfo=CN_TZ)
    three = plan_times(windows=windows, count=3, day=day, key="x")
    assert len(three) == 3
    buckets = [1 if m.hour >= 14 else 0 for m in three]
    assert buckets.count(0) == 2 and buckets.count(1) == 1, "3 次 2 段该是 2+1"


def test_只补已经到点的不排未来的():
    """排程器只补过去的点：未来的点等到了再说。

    否则运营中午改了时间段，也得等明天才生效 —— 而他改设置就是想今天就变。
    """
    windows = parse_windows("10-11,14-15")
    noon = datetime(2026, 9, 4, 12, 30, tzinfo=CN_TZ)
    due = due_times(windows=windows, count=2, now=noon, key="publish:1")
    assert len(due) == 1 and due[0].hour == 10, due
    evening = datetime(2026, 9, 4, 23, 0, tzinfo=CN_TZ)
    assert len(due_times(windows=windows, count=2, now=evening, key="publish:1")) == 2


def test_关掉就一条都不排():
    windows = parse_windows("10-11")
    day = datetime(2026, 9, 4, tzinfo=CN_TZ)
    assert plan_times(windows=windows, count=0, day=day, key="x") == []
    assert plan_times(windows=[], count=5, day=day, key="x") == []


def test_给人看的说法():
    assert describe(parse_windows("10-11,14-15")) == "10:00-11:00、14:00-15:00"


def test_跨零点的段会被丢掉_前端必须自己先拦住():
    """`22:00-02:00` 解析不出来，会被静默丢弃。

    这是刻意的：解析失败不该让排程崩掉。但对界面来说是个陷阱 ——
    运营填了一段、保存成功、然后什么都不发生。所以**前端要在保存前拦住**，
    这条测试把这个契约写下来，免得以后有人以为后端会支持跨零点。
    """
    assert parse_windows("22:00-02:00") == []
    assert parse_windows("10:00-11:00,22:00-02:00") == [(600, 660)]
    # 起止相同也一样丢掉（"10-10" 里没有任何一分钟可选）
    assert parse_windows("10:00-10:00") == []


def test_同一段里安排两次会挨得很近吗():
    """次数比段数多的时候，多出来的会回到前面的段里（i % 段数）。

    落点各自随机，所以**有可能挨得很近**。这是已知行为，不是 bug ——
    但界面上要提示运营"次数比时间段多"，否则他不会预料到同一个小时里推两条。
    """
    times = plan_times(
        windows=[(600, 660), (840, 900)], count=3,
        day=datetime(2026, 9, 4, tzinfo=CN_TZ), key="publish:1",
    )
    assert len(times) == 3
    hours = sorted(t.hour for t in times)
    assert hours.count(10) == 2 and hours.count(14) == 1
