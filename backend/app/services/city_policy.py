"""城市策略：自动发布的开关、时段、每天几篇，**按城市设**。

## 为什么要有这一层

改之前，同一条流水线四种粒度：

| 环节 | 粒度 |
|---|---|
| 自动生成内容 | 按城市（`supply:cities`） |
| 自动发布开关 | **按账号**，37 个各勾各的 |
| 发布时段 | **全局一个** |
| 每天几篇 | **全局一个** |

后果是三个具体的损失：
1. 加一个深圳号，得有人记得去勾 —— 生成那边按城市自动覆盖新号，发布这边不会，
   新号安安静静地不发东西，页面上一切正常；
2. 北京想换时段做不到，改了就连深圳一起改；
3. 深圳想 3 篇、北京想 1 篇，只能靠每个号的上限往下压，抬不上去。

## 解析顺序（唯一真相就在这三个函数里）

    账号例外（强制开/强制关） → 城市策略 → 兜底

- 开关兜底 = **账号自己的老开关** `DeviceAccount.auto_publish`
  —— 城市没设策略时，就是改之前的行为，一点不变。
- 时段、篇数兜底 = 全局设置（`publish:windows` / `publish:daily_target`）。

策略里每个字段都可以为空，**空 = 这一项不按城市设，用兜底**。所以一个城市
可以只单独设时段、开关和篇数继续跟全局走。

## 调用方只许用这三个函数

`publishes()` / `windows_for()` / `target_for()`。以前散落在 10 个地方的
`account.auto_publish`、`publish_windows(session)`、
`_expected_for(account, daily_target(session))` 都已换掉 —— 留任何一处直读，
那一处就是「城市关了它还在发」或「城市改了时段它还按全局排」。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models.entities import CityPolicy, DeviceAccount

# 账号例外的三态。None = 跟随城市（默认）。
OVERRIDE_ON = "on"
OVERRIDE_OFF = "off"
OVERRIDES = (OVERRIDE_ON, OVERRIDE_OFF)

# 这些不是城市，不能挂策略 —— 没设城市的号是漏配了，不是一个分组。
NOT_A_CITY = ("", "未分组", "通用")


def is_city(name: str | None) -> bool:
    return (name or "").strip() not in NOT_A_CITY


def policies_by_city(session: Session) -> dict[str, CityPolicy]:
    """一次查完。城市最多十来个，循环里反复调用前先拿这个 dict 传进去。"""
    return {p.city: p for p in session.exec(select(CityPolicy)).all()}


def policy_for(
    session: Session, city: str | None, pmap: dict[str, CityPolicy] | None = None
) -> CityPolicy | None:
    if not is_city(city):
        return None
    if pmap is not None:
        return pmap.get(city)
    return session.exec(select(CityPolicy).where(CityPolicy.city == city)).first()


# ─────────────────────────────────────────────────────────────────────
# 三个解析函数
# ─────────────────────────────────────────────────────────────────────
def publishes(
    session: Session,
    account: DeviceAccount,
    pmap: dict[str, CityPolicy] | None = None,
) -> bool:
    """这个号现在该不该自动发布。**只看开关，不看健康状况**（那是调用方的事）。"""
    override = (account.publish_override or "").strip()
    if override == OVERRIDE_ON:
        return True
    if override == OVERRIDE_OFF:
        return False
    policy = policy_for(session, account.city, pmap)
    if policy is not None and policy.auto_publish is not None:
        return bool(policy.auto_publish)
    # 城市没设开关 → 账号自己的老开关。这一行保证了上线当天行为零变化。
    return bool(account.auto_publish)


def windows_for(
    session: Session,
    account: DeviceAccount | None,
    pmap: dict[str, CityPolicy] | None = None,
) -> list[tuple[int, int]]:
    """这个号的发布时段。城市设了用城市的，没设用全局的。空 = 不限时段。"""
    from app.services.tasks import publish_windows
    from app.services.schedule_windows import parse_windows

    if account is not None:
        policy = policy_for(session, account.city, pmap)
        if policy is not None and policy.windows is not None:
            # 城市设成空串 = 这个城市明确不限时段（和「没设」不是一回事）
            return parse_windows(policy.windows)
    return publish_windows(session)


def city_target(
    session: Session, city: str | None, pmap: dict[str, CityPolicy] | None = None
) -> int:
    """这个城市每个号每天打算发几篇（还没被单号上限压过）。"""
    from app.services.notify_sweep import daily_target

    policy = policy_for(session, city, pmap)
    if policy is not None and policy.daily_target:
        return int(policy.daily_target)
    return daily_target(session)


def target_for(
    session: Session,
    account: DeviceAccount,
    pmap: dict[str, CityPolicy] | None = None,
) -> int:
    """这个号每天实际发几篇。

    - **城市明确设了篇数 → 就是它**，不再被单号上限压。
    - 城市跟全局 → min(全局目标, 这个号的上限)，和改之前完全一样。

    ⚠ 为什么城市设的篇数不能再取 min：`daily_quota` 在界面上根本改不了，
    而保存默认设置时 `set_publish_target` 会把所有号的上限**覆盖成全局值**，
    新号默认又是 2。取 min 的话，深圳设 5 篇实际永远是 3（或 2），
    而城市卡片和开城市的确认框都写着 5 —— 设置不生效、界面还在说假话。
    审查时被三个独立的 agent 分别抓到。生产上 37 个号的上限全是 3 = 全局，
    所以「跟全局」那一支上线当天零变化。
    """
    policy = policy_for(session, account.city, pmap)
    if policy is not None and policy.daily_target:
        return int(policy.daily_target)
    target = city_target(session, account.city, pmap)
    cap = account.daily_quota if account.daily_quota is not None else target
    return min(target, cap)


# ─────────────────────────────────────────────────────────────────────
# 写入
# ─────────────────────────────────────────────────────────────────────
_UNSET = object()


def set_policy(
    session: Session,
    city: str,
    *,
    auto_publish=_UNSET,
    windows=_UNSET,
    daily_target=_UNSET,
) -> CityPolicy:
    """只改传进来的字段。传 None = 这一项改回「跟随全局」。"""
    city = (city or "").strip()
    if not is_city(city):
        raise ValueError(f"「{city or '空'}」不是城市，不能单独设置")
    policy = policy_for(session, city) or CityPolicy(city=city)
    if auto_publish is not _UNSET:
        policy.auto_publish = auto_publish
    if windows is not _UNSET:
        policy.windows = windows
    if daily_target is not _UNSET:
        policy.daily_target = daily_target
    policy.updated_at = datetime.now(timezone.utc)
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy


def seed_from_accounts(session: Session) -> list[str]:
    """第一次上线时，把「现在各个号的开关」折算成城市策略。

    **只给全城意见一致的城市建策略**：一个城市里的号全开或全关，建出来的策略
    和现状完全等价；开关不一致的城市不建，那些号继续按各自的老开关走。
    所以这一步前后，没有任何一个号的发不发会变。

    时段和篇数一律不填（= 跟随全局），它们现在本来就是全局的。

    返回建了策略的城市名，给日志用。
    """
    existing = policies_by_city(session)
    by_city: dict[str, list[bool]] = {}
    for acc in session.exec(
        select(DeviceAccount).where(DeviceAccount.platform == "douyin")
    ).all():
        if not is_city(acc.city):
            continue
        by_city.setdefault(acc.city, []).append(bool(acc.auto_publish))
    made = []
    for city, flags in by_city.items():
        if city in existing:
            continue
        if all(flags) or not any(flags):
            session.add(CityPolicy(city=city, auto_publish=flags[0]))
            made.append(city)
    if made:
        session.commit()
    return made
