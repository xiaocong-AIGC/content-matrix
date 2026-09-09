import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AtSign,
  Check,
  Image as ImageIcon,
  RefreshCw,
  Square,
} from "lucide-react";

import {
  getBroadcastSchedule,
  getGroupCounts,
  previewBroadcast,
  saveBroadcastSchedule,
  setDeviceAutoBroadcast,
  stopBroadcastSchedule,
} from "../lib/api";
import type {
  BroadcastPreview,
  BroadcastPreviewSlot,
  BroadcastSchedule,
  Device,
} from "../lib/types";
import { Select } from "./Select";
import { TimeWindows, describeWindows } from "./TimeWindows";
import { useConfirm } from "./ConfirmModal";

/** 状态由后端算好。前端**不做**任何本地时间比较 ——
 *  运营的机器时区不保证是 +8，远程桌面更不保证。 */
const STATE_TEXT: Record<string, string> = {
  upcoming: "待推",
  due: "马上推",
  queued: "已排队",
  sent: "已推出",
  failed: "推送失败",
  cancelled: "已取消",
  missed: "过了时间，没推",
};

const GENERIC = ["通用", "全国", "未分组", ""];

interface Flat extends BroadcastPreviewSlot {
  account: string;
  device?: string | null;
  city?: string | null;
  blocked?: string | null;
}

interface Props {
  devices: Device[];
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
  onGoto: (view: "broadcast-library" | "devices") => void;
}

/**
 * 自动推送。
 *
 * 这一页九成的打开次数是**来看的**，不是来改的 —— 所以顺序是
 * 今天的安排 → 规则 → 哪些账号会推，频率从高到低。
 *
 * 中间那块警示区是这一页存在的理由：排程对「账号异常 / 读不到群 /
 * 没有可用消息」全是静默跳过，别处一点异常都看不出来 ——
 * 每天安安静静地推 0 条，和「推成功了」长得一模一样。
 *
 * ⚠ 这一页**不轮询**。全局刷新是 2 秒一轮，而预览要为每个账号算一遍时刻，
 * 挂进那个循环就是几十倍的查询量。这套系统吃过「更新数据阻塞心跳、
 * 20 台同时被判掉线」的亏。重拉只在：进页面 / 保存后 / 改开关后 / 手动点。
 */
export function AutoBroadcastView({ devices, onNotice, onGoto }: Props) {
  const [schedule, setSchedule] = useState<BroadcastSchedule>();
  const [preview, setPreview] = useState<BroadcastPreview>();
  const [groupCounts, setGroupCounts] = useState<Record<string, number>>({});
  const [count, setCount] = useState(2);
  const [windows, setWindows] = useState("");
  const [mode, setMode] = useState<"same" | "rotate">("same");
  const [cityFilter, setCityFilter] = useState("all");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [stopped, setStopped] = useState(false);
  const [showPast, setShowPast] = useState(false);
  const [confirm, confirmUI] = useConfirm();

  // ⚠ onNotice 是调用方内联写的箭头函数，**每次渲染都是新的**。直接把它写进
  // useCallback 的依赖里，reload 就会跟着变，`useEffect([reload])` 于是跟着
  // App 的 2 秒全局刷新一起重跑 —— 结果是这一页偷偷变成了 2 秒一次的轮询，
  // 而且每次都把运营正在编辑的时间段冲掉（实测：改完两秒就弹回原值，改不动）。
  const noticeRef = useRef(onNotice);
  noticeRef.current = onNotice;

  const reload = useCallback(
    async (quiet = false) => {
      try {
        const [s, p, g] = await Promise.all([
          getBroadcastSchedule(),
          previewBroadcast(),
          getGroupCounts().catch(() => ({})),
        ]);
        setSchedule(s);
        setPreview(p);
        setGroupCounts(g);
        if (!quiet) {
          setCount(s.daily_count || 2);
          setWindows(s.windows);
          setMode(s.mode);
        }
      } catch (error) {
        noticeRef.current(
          "error", "读取设置失败", error instanceof Error ? error.message : "",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  const on = schedule ? schedule.daily_count > 0 : false;
  const rows = preview?.rows ?? [];
  const accountsOn = rows.length;

  // 抖音账号 + 它们的开关，直接读 devices（全局每 2 秒刷新，免费且新鲜）
  const accounts = useMemo(
    () =>
      devices
        .flatMap((d) =>
          (d.accounts ?? [])
            .filter((a) => a.platform === "douyin")
            .map((a) => ({ device: d, account: a })),
        )
        .sort((x, y) =>
          `${x.account.city}${x.account.nickname}`.localeCompare(
            `${y.account.city}${y.account.nickname}`,
            "zh",
          ),
        ),
    [devices],
  );

  const cities = useMemo(() => {
    const set = new Set<string>();
    rows.forEach((r) => r.city && set.add(r.city));
    accounts.forEach(({ account }) => account.city && set.add(account.city));
    return [...set].filter((c) => !GENERIC.includes(c)).sort();
  }, [rows, accounts]);

  // 按时间拍平。不按账号分组：same 模式下 10 个账号 × 2 次会把同一句话重复 20 遍，
  // 而按时间排恰好回答运营真正问的那句「今天群里几点会冒出什么」。
  const flat: Flat[] = useMemo(() => {
    const out: Flat[] = [];
    rows.forEach((r) => {
      if (cityFilter !== "all" && r.city !== cityFilter) return;
      r.times.forEach((t) =>
        out.push({
          ...t,
          account: r.account,
          device: r.device,
          city: r.city,
          blocked: r.blocked,
        }),
      );
    });
    return out.sort((a, b) => a.time.localeCompare(b.time));
  }, [rows, cityFilter]);

  const done = flat.filter((f) =>
    ["sent", "failed", "cancelled", "missed"].includes(f.state),
  );
  const live = flat.filter(
    (f) => !["sent", "failed", "cancelled", "missed"].includes(f.state),
  );
  const nextTime = live.find((f) => f.state === "upcoming")?.time;

  const blockedAccounts = rows.filter((r) => r.blocked);
  const noGroups = blockedAccounts.filter((r) => !r.groups);
  const illAccounts = blockedAccounts.filter((r) => r.health !== "normal");
  const noMessages = blockedAccounts.filter(
    (r) => r.groups && r.health === "normal" && !r.usable_messages,
  );
  const missed = flat.filter((f) => f.state === "missed").length;

  async function save(nextCount = count) {
    setSaving(true);
    try {
      const s = await saveBroadcastSchedule({
        daily_count: nextCount,
        windows,
        mode,
      });
      setSchedule(s);
      setCount(s.daily_count || nextCount);
      setStopped(false);
      await reload(true);
      onNotice(
        "success",
        "已保存",
        s.accounts_on.length
          ? `每天 ${s.daily_count} 次，${s.windows_readable}，${s.accounts_on.length} 个账号会推。`
          : "但还没有账号打开自动推送，今天不会推。",
      );
    } catch (error) {
      onNotice("error", "保存失败", error instanceof Error ? error.message : "");
    } finally {
      setSaving(false);
    }
  }

  async function stopNow() {
    const upcoming = preview?.summary.upcoming ?? 0;
    const queued = schedule?.queued_now ?? 0;
    const ok = await confirm({
      title: "立刻停止自动推送？",
      danger: true,
      detail:
        (upcoming || queued
          ? `今天还没到点的 ${upcoming} 条不再推，已经排进队列的 ${queued} 条也一起取消。正在发送的那条停不下来。`
          : "今天没有排着的推送，停止后也不会再排新的。") +
        "\n\n停止后今天不会再排，需要重新设次数才恢复；今天取消掉的不会补回来。",
      confirmText: "停止推送",
    });
    if (!ok) return;
    try {
      const res = await stopBroadcastSchedule();
      setSchedule(res);
      setCount(2);
      setStopped(true);
      await reload(true);
      onNotice(
        "success",
        "已停止",
        res.in_flight
          ? `取消了 ${res.cancelled} 条。还有 ${res.in_flight} 条正在发送，停不下来。`
          : `取消了 ${res.cancelled} 条，自动推送已关闭。`,
      );
    } catch (error) {
      onNotice("error", "停止失败", error instanceof Error ? error.message : "");
    }
  }

  async function toggleAccount(
    deviceId: number,
    nickname: string,
    next: boolean,
    groups: number,
  ) {
    if (next) {
      const ok = await confirm({
        title: `打开 ${nickname} 的自动推送？`,
        detail:
          `打开后，这个账号每天 ${count} 次，把消息发到它的 ${groups} 个群。` +
          (groups ? "" : "\n\n这个账号还没有读到群，打开了也不会推。"),
        confirmText: "打开",
      });
      if (!ok) return;
    }
    try {
      await setDeviceAutoBroadcast(deviceId, next);
      await reload(true);
      if (!next) {
        onNotice(
          "success",
          "已关闭",
          `${nickname} 不再自动推送。今天已经排进队列的还会推完，要立刻停用上面的「立刻停止」。`,
        );
      }
    } catch (error) {
      onNotice("error", "操作失败", error instanceof Error ? error.message : "");
    }
  }

  function renderSlot(f: Flat, i: number) {
    return (
      <tr key={`${f.account}-${f.time}-${i}`} className={f.blocked ? "row-muted" : ""}>
        <td>
          <b>{f.time}</b>
          {f.time === nextTime ? <span className="msg-chip">下一条</span> : null}
        </td>
        <td>
          {f.account}
          {f.device ? <small className="tw-sep">{f.device}</small> : null}
        </td>
        <td>
          <span
            className={`content-city ${
              !f.city || GENERIC.includes(f.city) ? "generic" : ""
            }`}
          >
            {f.city || "未分组"}
          </span>
        </td>
        <td>
          <span className="slot-text" title={f.text}>
            {f.text || "（挑不到消息）"}
          </span>
          {f.has_image ? (
            <span className="msg-chip">
              <ImageIcon size={11} /> 带图
            </span>
          ) : null}
          {f.mention_all ? (
            <span className="msg-chip">
              <AtSign size={11} /> @所有人
            </span>
          ) : null}
          {f.orphan ? <span className="msg-chip off">计划外</span> : null}
          {!f.certain && !f.task_id ? (
            <span className="msg-chip off">预计</span>
          ) : null}
        </td>
        <td>
          <span className={`slot-state s-${f.state}`}>{STATE_TEXT[f.state]}</span>
          {f.blocked ? <span className="msg-chip off">{f.blocked}</span> : null}
        </td>
      </tr>
    );
  }

  return (
    <div className="content-page page-enter">
      <header className="catalog-hero broadcast-hero">
        <div>
          <span>群推送</span>
          <h2>每天到点，自动把消息发到群里。</h2>
          <p>
            从消息库里挑一条，在你设定的时间段里随机一个时刻发出。
            只发给打开了自动推送的账号。今天几点推、推哪一条，下面都能看到。
          </p>
        </div>
        <div className="hero-stats">
          <div className="hero-stat accent">
            <strong>{preview?.summary.upcoming ?? "—"}</strong>
            <small>今天还要推</small>
          </div>
          <div className="hero-stat">
            <strong>{accountsOn}</strong>
            <small>参与的账号</small>
          </div>
          <div className="hero-stat">
            <strong>{schedule?.daily_count || "关"}</strong>
            <small>每天次数</small>
          </div>
        </div>
      </header>

      {/* 这些情况排程全是静默跳过，别处一点异常都看不出来 */}
      <section>
        {on && schedule && !schedule.usable_messages ? (
          <p className="library-warn danger">
            自动推送开着（每天 {schedule.daily_count} 次，{schedule.windows_readable}），
            但库里没有一条启用的消息 —— 到点会没东西可发。
            <button className="button secondary" onClick={() => onGoto("broadcast-library")}>
              去消息库
            </button>
          </p>
        ) : null}
        {on && !accountsOn ? (
          <p className="library-warn danger">
            还没有账号打开自动推送，到点不会发给任何人。
          </p>
        ) : null}
        {noGroups.length ? (
          <p className="library-warn">
            {noGroups.map((r) => r.account).join("、")} 读不到群，这些账号今天不会推。
            去设备列表重新扫描群。
            <button className="button secondary" onClick={() => onGoto("devices")}>
              去设备列表
            </button>
          </p>
        ) : null}
        {illAccounts.length ? (
          <p className="library-warn">
            {illAccounts.map((r) => r.account).join("、")} 账号异常，今天不会推。
            修好之后会自动恢复。
          </p>
        ) : null}
        {noMessages.length ? (
          <p className="library-warn">
            {noMessages.map((r) => r.account).join("、")} 没有可推的消息：
            库里既没有它所在城市的，也没有通用的。
          </p>
        ) : null}
        {missed ? (
          <p className="library-warn">
            今天有 {missed} 条到点没推出去 —— 当时手机在忙或者不在线。
          </p>
        ) : null}
        {stopped ? (
          <p className="library-warn">
            自动推送已停止，今天不会再推。把每天次数设回去并保存即可恢复；
            今天已经取消掉的那几条不会补回来。
          </p>
        ) : null}
      </section>

      {/* ── 今天的安排：全页第一优先，排在配置之前 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>今天</span>
            <h2>几点推、推哪一条</h2>
          </div>
          <div className="record-filters">
            <small>时刻是提前算好的，这里看到的就是会发出的时间和内容。</small>
            {cities.length ? (
              <Select
                label="城市"
                value={cityFilter}
                options={[
                  { value: "all", label: "全部城市" },
                  ...cities.map((c) => ({ value: c, label: c })),
                ]}
                onChange={setCityFilter}
              />
            ) : null}
            <button className="ghost-icon" title="刷新" onClick={() => void reload(true)}>
              <RefreshCw size={15} />
            </button>
            {on ? (
              <button className="button secondary danger-text" onClick={stopNow}>
                <Square size={13} /> 立刻停止
              </button>
            ) : null}
          </div>
        </header>

        {/* 没安排的时候不说这句 —— 「今天所有账号推的是同一条：「—」」是句废话 */}
        {preview && flat.length ? (
          <p className="supply-hint">
            {preview.mode === "same"
              ? `今天所有账号推的是同一条：「${((live[0] ?? done[0])?.text ?? "").slice(0, 40)}」`
              : "每个账号各推各的，优先推库里推得少的。还没到的时间点，内容可能随当天的推送情况变化。"}
          </p>
        ) : null}

        {loading ? (
          <p className="content-block-empty">正在读取今天的安排…</p>
        ) : !on ? (
          <p className="content-block-empty">
            自动推送还没打开。在下面设定每天推几次和时间段，这里会列出今天的安排。
          </p>
        ) : !accountsOn ? (
          <p className="content-block-empty">
            还没有账号打开自动推送，今天不会推。在下面「哪些账号会推」里打开需要的账号。
          </p>
        ) : !flat.length ? (
          <p className="content-block-empty">这个筛选下没有安排。</p>
        ) : (
          <>
            {done.length ? (
              <p className="supply-hint">
                已经过去 {done.length} 条：推出 {done.filter((d) => d.state === "sent").length}、
                没推 {done.length - done.filter((d) => d.state === "sent").length}。
                <button className="linklike" onClick={() => setShowPast((v) => !v)}>
                  {showPast ? "收起" : "点开查看"}
                </button>
              </p>
            ) : null}
            <table className="supply-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>账号</th>
                  <th>城市</th>
                  <th>消息</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {(showPast ? done : []).map(renderSlot)}
                {live.map(renderSlot)}
              </tbody>
            </table>
          </>
        )}

        <div className="supply-actions">
          <small>
            到点时手机正在发作品的话，群消息会等它发完，通常晚几分钟。
            排了超过 6 小时还没发出去的就不再发了。
          </small>
        </div>
      </section>

      {/* ── 规则 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>规则</span>
            <h2>每天推几次，什么时候推</h2>
          </div>
        </header>

        <label className={`supply-switch ${on ? "on" : ""}`}>
          <input
            type="checkbox"
            checked={on}
            onChange={(e) => {
              // ⚠ 关掉必须走 stop，不能 PUT daily_count=0：那只写设置、不碰队列，
              // 运营点了关、群里还在冒消息。
              if (e.target.checked) void save(count || 2);
              else void stopNow();
            }}
          />
          <span>
            <strong>自动推送</strong>
            <small>
              {on && schedule
                ? `已打开。每天 ${schedule.daily_count} 次，时刻在 ${schedule.windows_readable} 里随机。`
                : "已关闭。不会自动推送，需要发的时候去临时群发。"}
            </small>
          </span>
        </label>

        <div className="supply-fields">
          <label className="field">
            <span>每天推送次数</span>
            <Select
              variant="field"
              value={String(count)}
              options={[1, 2, 3, 4, 5, 6].map((n) => ({
                value: String(n),
                label: `${n} 次`,
              }))}
              onChange={(v) => setCount(Number(v))}
            />
            <small className="field-note">
              每个账号每天各推这么多次。同城的不同账号不会撞在同一分钟。
            </small>
          </label>
          <label className="field">
            <span>挑消息的方式</span>
            <Select
              variant="field"
              value={mode}
              options={[
                { value: "same", label: "所有账号发同一条" },
                { value: "rotate", label: "每个账号各发一条" },
              ]}
              onChange={(v) => setMode(v as "same" | "rotate")}
            />
            <small className="field-note">
              {mode === "same"
                ? "当天所有账号推同一条，每天换一条。都优先挑本城的消息，本城没有才用通用的。"
                : "每个账号各挑一条，不同的群看到的不一样。都优先挑本城的消息，本城没有才用通用的。"}
            </small>
          </label>
        </div>

        <div className="field span-2">
          <span>推送时间段</span>
          <TimeWindows
            value={windows}
            onChange={setWindows}
            count={count}
            noun="推送"
            emptyHint="还没有时间段，加一个才会推送。"
          />
        </div>

        <div className="supply-actions">
          <button className="button primary" disabled={saving} onClick={() => void save()}>
            <Check size={15} /> 保存设置
          </button>
          <small>
            现在设的是：每天 {count} 次，{describeWindows(windows) || "还没有时间段"}
          </small>
        </div>
      </section>

      {/* ── 哪些账号会推 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>账号</span>
            <h2>哪些账号会推</h2>
          </div>
          <small>只有打开的账号会推，上面的设置不会自动套到所有账号。</small>
        </header>
        <p className="supply-hint">
          打开之后，这个账号每天按上面的设定，把消息发到它所在的全部群。
        </p>

        <div className="kv-rows">
          {!accounts.length ? (
            <p className="content-block-empty">还没有抖音账号。先在设备列表里添加。</p>
          ) : (
            accounts
              .filter(
                ({ account }) => cityFilter === "all" || account.city === cityFilter,
              )
              .map(({ device, account }) => {
                const groups = groupCounts[String(device.id)] ?? 0;
                const nickname = account.nickname || device.name;
                return (
                  <div className="kv-row account-row" key={account.id}>
                    <span className="account-name">{nickname}</span>
                    <span className="account-meta">
                      <span
                        className={`content-city ${
                          GENERIC.includes(account.city) ? "generic" : ""
                        }`}
                      >
                        {account.city || "未分组"}
                      </span>
                      <small>{device.name}</small>
                      <small>{groups} 个群</small>
                      {!groups ? <span className="msg-chip off">读不到群</span> : null}
                      {account.health !== "normal" ? (
                        <span className="msg-chip off">账号异常</span>
                      ) : null}
                      {account.logged_in === false ? (
                        <span className="msg-chip off">未登录</span>
                      ) : null}
                    </span>
                    <button
                      className={`auto-toggle ${account.auto_broadcast ? "on" : ""}`}
                      onClick={() =>
                        void toggleAccount(
                          device.id,
                          nickname,
                          !account.auto_broadcast,
                          groups,
                        )
                      }
                    >
                      <span className="auto-toggle-track">
                        <i />
                      </span>
                      {account.auto_broadcast ? "自动推送" : "不参与"}
                    </button>
                  </div>
                );
              })
          )}
        </div>
      </section>
      {confirmUI}
    </div>
  );
}
