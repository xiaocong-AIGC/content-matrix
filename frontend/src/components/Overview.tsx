import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  Library,
  MapPin,
  Layers,
  Radar,
  RefreshCw,
  Search,
  Send,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Trash2,
  X,
} from "lucide-react";

import { detectDevices, getAccountRoster, refreshAccount } from "../lib/api";
import type { Device, DeviceAccount } from "../lib/types";
import { usePersistentState } from "../lib/usePersistentState";
import { FilterDropdown } from "./FilterDropdown";

import { useConfirm } from "./ConfirmModal";
interface Props {
  devices: Device[];
  contentPending: number;
  onSchedule: (device: Device, account: DeviceAccount) => void;
  onResolveHealth: (device: Device, account: DeviceAccount) => void;
  onSelectDevice: (id: number, platform?: string) => void;
  onSetCity: (device: Device, account: DeviceAccount) => void;
  onDeleteAccount: (device: Device, account: DeviceAccount) => void;
  onBatchSchedule: () => void;
}

const PLATFORM_LABEL: Record<string, string> = { douyin: "抖音", xhs: "小红书" };

/** 最后一次心跳是什么时候。
 *
 * 离线时只写一句不带时间的「数据截至上次在线」，看不出这台是刚掉的
 * 还是掉了两天 —— 而这正是「等一下」和「该去重启了」的分界。
 * ⚠ 后端存的是 naive UTC，靠 normalize_datetimes 补 Z，直接 new Date() 就对；
 * 自己拼字符串解析容易差 8 小时。 */
function lastSeen(device: Device): string {
  const raw = device.last_heartbeat_at;
  if (!raw) return "未知";
  const t = new Date(raw);
  if (Number.isNaN(t.getTime())) return "未知";
  const now = new Date();
  const sameDay =
    t.getFullYear() === now.getFullYear() &&
    t.getMonth() === now.getMonth() &&
    t.getDate() === now.getDate();
  const hm = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(t);
  if (sameDay) return `今天 ${hm}`;
  return `${t.getMonth() + 1}月${t.getDate()}日 ${hm}`;
}

interface Row {
  device: Device;
  account: DeviceAccount;
}

// Flatten devices into one row per platform account (a phone with douyin + xhs
// shows as two cards). Falls back to a synthesized douyin account for older
// payloads without an accounts array.
function accountRows(devices: Device[]): Row[] {
  const rows: Row[] = [];
  for (const device of devices) {
    const accts =
      device.accounts && device.accounts.length
        ? device.accounts
        : [
            {
              id: -device.id,
              device_id: device.id,
              platform: "douyin",
              nickname: device.douyin_nickname ?? null,
              account_id: device.douyin_id ?? null,
              city: device.city || "未分组",
              auto_publish: device.auto_publish ?? false,
              daily_quota: device.daily_quota ?? 2,
              health: device.health ?? "normal",
              health_message: device.health_message ?? null,
              today_published: device.today_published ?? 0,
            } as DeviceAccount,
          ];
    for (const account of accts) rows.push({ device, account });
  }
  return rows;
}

function isOnline(device: Device) {
  return device.status === "online" || device.status === "busy";
}

export function Overview({
  devices,
  contentPending,
  onSchedule,
  onResolveHealth,
  onSelectDevice,
  onSetCity,
  onDeleteAccount,
  onBatchSchedule,
}: Props) {
  // 筛选状态持久化：切到别的页面再回来，选中的分组/平台/开关都还在（这些
  // 视图是条件渲染的，普通 useState 一卸载就重置）。见 usePersistentState。
  const [cityFilter, setCityFilter] = usePersistentState<string>(
    "overview.city",
    "全部",
  );
  const [platformFilter, setPlatformFilter] = usePersistentState<string>(
    "overview.platform",
    "全部",
  );
  // 默认只看"在用"的账号：在线且未检测到未登录。矩阵接了 20 台但没全用，
  // 全列出来又乱又难看。关掉这个开关才显示全部（含离线/未登录）。
  const [activeOnly, setActiveOnly] = usePersistentState<boolean>(
    "overview.activeOnly",
    true,
  );
  // 按名字找账号。60 台以上这是必需的 —— 现在只能靠肉眼在卡片墙里扫。
  // 三个字段一起匹配：昵称、平台号、设备编号（要 adb 连一台机器时，编号就是地址）。
  const [keyword, setKeyword] = useState("");
  const [detecting, setDetecting] = useState(false);
  // 「在跑的号」和「今日目标」都由后端给 —— 定义只有一份，见 GET /devices/roster。
  // 自己在前端再算一遍，就是首页和企微群消息对不上的由来。
  const [roster, setRoster] = useState<{
    ids: Set<number>; target: number; perHead: number;
  }>({ ids: new Set(), target: 0, perHead: 1 });
  useEffect(() => {
    getAccountRoster()
      .then((r) =>
        setRoster({
          ids: new Set(r.in_service_ids),
          target: r.today_target,
          perHead: r.daily_target || 1,
        }),
      )
      .catch(() => undefined);
  }, []);
  const [detectMsg, setDetectMsg] = useState("");
  // 已下发「更新账号」指令的设备 id：按钮转圈，提示已排队等设备心跳重读。
  const [refreshingIds, setRefreshingIds] = useState<number[]>([]);
  const [confirm, confirmUI] = useConfirm();
  const rows = accountRows(devices);

  async function handleRefreshAccount(device: Device) {
    if (refreshingIds.includes(device.id)) return;
    setRefreshingIds((ids) => [...ids, device.id]);
    try {
      await refreshAccount(device.id);
      setDetectMsg(
        `已通知「${device.name}」重新识别登录的账号，约 10 秒后昵称、账号和群列表会更新`,
      );
    } catch (reason) {
      setDetectMsg(reason instanceof Error ? reason.message : "更新账号失败");
    } finally {
      setTimeout(
        () => setRefreshingIds((ids) => ids.filter((x) => x !== device.id)),
        8000,
      );
    }
  }

  async function handleDetect() {
    setDetecting(true);
    setDetectMsg("");
    try {
      const r = await detectDevices();
      // ok=false 时以前也照报「无新设备需要装机」，把装机程序压根没跑起来
      // 说成"没有新设备"——扩容时最容易被这句话骗住，所以失败要如实说。
      const tail = (r.output || "").trim().split("\n").pop() ?? "";
      setDetectMsg(
        !r.ok
          ? `检测异常：装机程序未正常执行${tail ? `（${tail}）` : ""}`
          : r.newly_provisioned > 0
            ? `检测完成：新装机 ${r.newly_provisioned} 台，共 ${r.connected} 台已连接。新装机的手机就在下面，账号信息约 1 分钟后自动读取。`
            : `检测完成：${r.connected} 台已连接，无新设备需要装机`,
      );
    } catch (reason) {
      setDetectMsg(reason instanceof Error ? reason.message : "检测失败");
    } finally {
      setDetecting(false);
    }
  }
  const cities = ["全部", ...new Set(rows.map((r) => r.account.city || "未分组"))];
  const platforms = ["全部", ...new Set(rows.map((r) => r.account.platform))];
  // "在用" = 设备在线 且 没被判为未登录（null=未探测的先保留，避免误藏）。
  // 「在用」和 KPI 的分母必须是同一批号，否则同一屏里「仅在用账号 · 31」和
  // 「今日已发 7 / 14」自相矛盾 —— 这正是改之前那版的毛病，只是数字换了一对。
  // 名册由后端给（GET /devices/roster），和企微群通知同一份口径。
  // ⚠ 名册目前只覆盖抖音（后端 _active_accounts 写死了 platform == "douyin"）。
  // 不加这道守卫的话，接了小红书之后每一个小红书号都不在 roster.ids 里，
  // 而 roster.ids.size > 0 会让下面那个兜底分支永远走不到 —— 小红书账号
  // 在首页会默认全部消失。今天全是抖音，这一行不改变任何行为，
  // 但它是接小红书那天不出事的前提。
  const rosterCovers = (platform: string) => platform === "douyin";
  const isActive = (r: Row) =>
    roster.ids.size && rosterCovers(r.account.platform)
      ? roster.ids.has(r.account.id)
      : isOnline(r.device) && r.account.logged_in !== false;

  // 🔴 这些号**即使不在发布名册里也必须看得见**。
  // 名册的判据是「最近 48 小时成功发过」，于是被封禁三天的号、掉登录的号、
  // 刚装机还没读到账号的手机，全都从默认视图里消失了 ——
  // 线上实测 37 个账号里藏掉 23 个，其中就有一个 health=abnormal 的封禁号。
  // 而「异常账号」这个 KPI 的分母以前也是名册，所以首页会写「账号都正常」，
  // 那个被封的号在首页上根本不存在。
  const mustShow = (r: Row) =>
    (r.account.health ?? "normal") !== "normal" ||
    r.account.logged_in === false ||
    // 开着自动发布的号**一定**要看得见：它正在替你往抖音发东西，
    // 却因为"最近两天没通过我们的应用发过"被默认视图滤掉 —— 管不了看不见的东西。
    // （实测：深圳「海风日记」开着自动发布，账号总览里搜得到、列表里没有。）
    r.account.auto_publish === true ||
    r.account.id < 0; // 还没读到账号的手机，Overview 会造一个负 id 的占位
  const visible = (r: Row) => isActive(r) || mustShow(r);

  const activeCount = rows.filter(visible).length;
  const hiddenCount = rows.filter((r) => !visible(r)).length;
  const key = keyword.trim().toLowerCase();
  const matches = (r: Row) =>
    !key ||
    [r.account.nickname, r.account.account_id, r.device.name, r.device.device_code]
      .some((v) => (v ?? "").toLowerCase().includes(key));
  const shownRows = rows.filter(
    (r) =>
      // 搜索时不受「在发布的账号」开关限制 —— 特意去找一个号，
      // 结果因为它两天没发而搜不到，是最让人火大的一种"找不到"。
      (!activeOnly || key !== "" || visible(r)) &&
      matches(r) &&
      (cityFilter === "全部" || (r.account.city || "未分组") === cityFilter) &&
      (platformFilter === "全部" || r.account.platform === platformFilter),
  );
  // ⚠ 分母只能算**在跑的号**。以前这四个数全是对 rows（全部 37 台设备的所有账号）
  // 求和，于是「今日已发 7 / 44」里的 44 是假的 —— 系统里实际只有十几个号在跑，
  // 另外那些走别的方式发布。最刺眼的是同一屏里「仅在用账号」一切，卡片只剩十几张，
  // KPI 分母还是 44，首页在自己打自己。企微群里报的是正确口径，两边对不上。
  //
  // 而且「在跑」不能用「在线」来判：心跳一断今日目标就跳水，运营会看到目标数
  // 分钟级晃动。产能分母用「已登录 + 开了自动发布」，不看在线；在线只用来算异常。
  // 和上面的「仅在用」同一个判据，不留两套
  const inService = rows.filter(isActive);
  const onlineAccounts = inService.filter((r) => isOnline(r.device));
  // ⚠ 异常用 visible、产能用 inService，两个问题两个分母，这是刻意的：
  // 一个封禁三天的号不在发布队列里，不该进产能分母（算进去首页和企微群会对不上），
  // 但它必须出现在「异常账号」里，否则首页写着「账号都正常」而实际有号被封。
  const abnormal = rows.filter(
    (r) => visible(r) && (r.account.health ?? "normal") !== "normal",
  );
  const needLogin = rows.filter((r) => r.account.logged_in === false);
  const todayPublished = inService.reduce(
    (sum, r) => sum + (r.account.today_published ?? 0),
    0,
  );
  // 目标由后端算好（每号每天几篇 × 在跑的号），不是 daily_quota —— 那是上限，
  // 用它算会把目标翻倍
  const todayTarget = roster.target || inService.length * roster.perHead;
  const runway =
    todayTarget > 0 ? Math.floor(contentPending / todayTarget) : null;

  return (
    <div className="overview-page page-enter">
      {confirmUI}
      <section className="kpi-strip">
        <div className="kpi">
          <span className="kpi-label">
            <Smartphone size={14} /> 在线账号
          </span>
          <strong>{onlineAccounts.length}</strong>
          <small>共 {rows.length} 个账号 · {devices.length} 台设备</small>
        </div>
        <div className="kpi">
          <span className="kpi-label">
            <Send size={14} /> 今日已发
          </span>
          <strong>
            {todayPublished}
            <i> / {todayTarget || "0"}</i>
          </strong>
          <small>
            {inService.length} 个账号在发，
            {new Set(inService.map((r) => r.account.target ?? roster.perHead)).size > 1
              ? "各城市篇数不一样"
              : `每个账号 ${inService[0]?.account.target ?? roster.perHead} 篇`}
          </small>
        </div>
        <div className="kpi">
          <span className="kpi-label">
            <Library size={14} /> 内容库待发
          </span>
          <strong>{contentPending}</strong>
          <small>
            {runway === null ? "还没有账号在发" : `按现在的节奏还够发 ${runway} 天`}
          </small>
        </div>
        <div className={`kpi ${abnormal.length ? "kpi-warn" : ""}`}>
          <span className="kpi-label">
            <AlertTriangle size={14} /> 异常账号
          </span>
          <strong>{abnormal.length}</strong>
          <small>
            {abnormal.length
              ? "已暂停发布，处理后可恢复"
              : needLogin.length
                ? `${needLogin.length} 个账号需要重新登录`
                : "账号都正常"}
          </small>
        </div>
      </section>

      <header className="section-heading overview-heading">
        <h2>账号矩阵</h2>
        <div className="overview-filters">
          <button
            type="button"
            className="detect-btn"
            onClick={() => void handleDetect()}
            disabled={detecting}
          >
            <Radar size={15} className={detecting ? "spin" : ""} />
            {detecting ? "检测中…" : "检测设备"}
          </button>
          <button
            type="button"
            className="detect-btn"
            onClick={onBatchSchedule}
            title="一次给多个账号排期，内容自动分配、不重复"
          >
            <Layers size={15} />
            批量排期
          </button>
          <label className="overview-search">
            <Search size={14} />
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索账号昵称、抖音号或设备编号"
            />
            {keyword ? (
              <button
                type="button"
                className="overview-search-clear"
                aria-label="清空搜索"
                onClick={() => setKeyword("")}
              >
                <X size={13} />
              </button>
            ) : null}
          </label>
          <button
            type="button"
            className={`active-toggle ${activeOnly ? "on" : ""}`}
            onClick={() => setActiveOnly((v) => !v)}
            title="只显示最近两天在发布的账号"
          >
            {activeOnly ? `在发布的账号 · ${activeCount}` : `全部账号 · ${rows.length}`}
          </button>
          {/* 默认视图会藏号 —— 这件事今天在界面上完全看不出来。说出来，并且能一键看全。 */}
          {activeOnly && !key && hiddenCount > 0 ? (
            <button
              type="button"
              className="overview-hidden-note"
              onClick={() => setActiveOnly(false)}
            >
              另有 {hiddenCount} 个账号最近两天没有发布，点击查看
            </button>
          ) : null}
          {platforms.length > 2 ? (
            <FilterDropdown
              label="平台"
              value={platformFilter}
              onChange={setPlatformFilter}
              options={platforms.map((p) => ({
                value: p,
                label: p === "全部" ? "全部平台" : (PLATFORM_LABEL[p] ?? p),
                count:
                  p === "全部"
                    ? rows.length
                    : rows.filter((r) => r.account.platform === p).length,
              }))}
            />
          ) : null}
          {cities.length > 1 ? (
            <FilterDropdown
              label="城市"
              icon={<MapPin size={13} />}
              value={cityFilter}
              onChange={setCityFilter}
              options={cities.map((c) => ({
                value: c,
                label: c,
                count:
                  c === "全部"
                    ? rows.length
                    : rows.filter((r) => (r.account.city || "未分组") === c)
                        .length,
              }))}
            />
          ) : null}
          <small className="overview-count">{shownRows.length} 个账号</small>
        </div>
      </header>
      {detectMsg ? <div className="overview-detect-note">{detectMsg}</div> : null}

      {rows.length ? (
        <section className="account-grid">
          {shownRows.map(({ device, account }) => {
            // 卡片上比的是**今日目标**，不是 daily_quota（那是上限）。
            // 上面 KPI 刚说完「每号每天 1 篇」，卡片却写「1 / 2」，
            // 同一屏两个口径 —— 上限不是这里要回答的问题，它也不能在这页改。
            // 每个号按它自己的篇数比 —— 城市可以单独设篇数，全站一个数已经不成立了。
            // 后端算好下发（account.target），老后端没有这个字段就退回全局。
            const dq = account.target ?? roster.perHead;
            const done = account.today_published ?? 0;
            const reached = done >= dq;
            const abnormalAcct = (account.health ?? "normal") !== "normal";
            const online = isOnline(device);
            return (
              <article
                key={`${device.id}-${account.platform}`}
                className={`account-card ${abnormalAcct ? "account-abnormal" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => onSelectDevice(device.id, account.platform)}
                onKeyDown={(event) => {
                  if (event.key === "Enter")
                    onSelectDevice(device.id, account.platform);
                }}
              >
                <header className="account-card-head">
                  <div
                    className={`account-status ${online ? "on" : ""} ${
                      online && device.accessibility_ok === false ? "notready" : ""
                    }`}
                    title={
                      online && device.accessibility_ok === false
                        ? "无障碍未绑定：设备在线但无法执行任务，请重启手机"
                        : ""
                    }
                  >
                    <i />
                    {device.status === "busy"
                      ? "执行中"
                      : !online
                        ? "离线"
                        : device.accessibility_ok === false
                          ? "仅在线·未就绪"
                          : device.accessibility_ok === true
                            ? "就绪"
                            : "在线"}
                  </div>
                  <button
                    type="button"
                    className="account-del"
                    title={
                      online
                        ? "更新账号：重新识别这台手机登录的账号和群，换账号后用，不影响发布"
                        : "设备离线，无法更新账号"
                    }
                    disabled={refreshingIds.includes(device.id) || !online}
                    onClick={(event) => {
                      event.stopPropagation();
                      handleRefreshAccount(device);
                    }}
                  >
                    <RefreshCw
                      size={14}
                      className={
                        refreshingIds.includes(device.id) ? "spin" : undefined
                      }
                    />
                  </button>
                  <button
                    type="button"
                    className="account-del"
                    title={`删除这个${PLATFORM_LABEL[account.platform] ?? ""}账号（该设备的其他账号不受影响）`}
                    onClick={async (event) => {
                      event.stopPropagation();
                      const label = PLATFORM_LABEL[account.platform] ?? account.platform;
                      const sole =
                        (device.accounts?.length ?? 1) <= 1;
                      const msg = sole
                        ? `删除设备「${device.name}」的${label}账号？这是它唯一的账号，设备也会一起移除，重新检测后会再出现。`
                        : `删除「${account.nickname || device.name}」的${label}账号？该设备的其他平台账号不受影响。`;
                      if (
                        await confirm({
                          title: `删除${label}账号？`,
                          detail: msg,
                          confirmText: "删除",
                          danger: true,
                        })
                      ) {
                        onDeleteAccount(device, account);
                      }
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                  <span className={`platform-badge plat-${account.platform}`}>
                    {PLATFORM_LABEL[account.platform] ?? account.platform}
                  </span>
                </header>

                <div className="account-id">
                  <div className="account-id-main">
                    <strong>
                      {account.nickname || device.name}
                      {account.logged_in === false ? (
                        <em className="acct-logout">未登录</em>
                      ) : null}
                    </strong>
                    <small>
                      {account.account_id
                        ? `${PLATFORM_LABEL[account.platform] ?? ""}号 ${account.account_id}`
                        : "还没读到登录的账号"}
                      {device.device_code ? (
                        <em className="agent-ver" title="设备编号，要用 adb 连这台手机时用它">
                          {" · "}{device.device_code}
                        </em>
                      ) : null}
                      {device.agent_version ? (
                        <em className="agent-ver" title="这台设备的 Agent 版本，升级后可以核对哪些设备已更新">
                          {" "}v{device.agent_version}
                        </em>
                      ) : null}
                    </small>
                  </div>
                  <button
                    className="city-tag"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSetCity(device, account);
                    }}
                    title="设置城市分组"
                  >
                    <MapPin size={13} /> {account.city || "未分组"}
                  </button>
                </div>

                {abnormalAcct ? (
                  <div className="account-health">
                    <ShieldAlert size={15} />
                    <span>{account.health_message || "账号状态异常，已暂停发布"}</span>
                  </div>
                ) : null}

                <div className="account-today">
                  <span>今日发布</span>
                  <strong className={reached ? "done" : ""}>
                    {done} / {dq}
                  </strong>
                </div>
                {!online ? (
                  <p className="account-stale">最后在线 {lastSeen(device)}</p>
                ) : null}

                {abnormalAcct ? (
                  <button
                    className="schedule-btn resolve"
                    onClick={(event) => {
                      event.stopPropagation();
                      onResolveHealth(device, account);
                    }}
                  >
                    <ShieldCheck size={15} />
                    已处理，恢复正常
                  </button>
                ) : (
                  <button
                    className="schedule-btn"
                    onClick={(event) => {
                      event.stopPropagation();
                      onSchedule(device, account);
                    }}
                  >
                    <CalendarClock size={15} />
                    排期发布
                  </button>
                )}
              </article>
            );
          })}
        </section>
      ) : (
        <div className="catalog-empty">
          <Smartphone size={42} strokeWidth={1.2} />
          <h3>还没有账号</h3>
          <p>在手机上启动 Agent 后，设备会自动出现在这里。</p>
        </div>
      )}
    </div>
  );
}
