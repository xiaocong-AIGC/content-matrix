import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, RefreshCw, Square } from "lucide-react";

import {
  getAccountRoster,
  getPublishTarget,
  getPublishWindows,
  getSupply,
  savePublishWindows,
  setDeviceAutoPublish,
  setPublishTarget,
} from "../lib/api";
import type { Device, SupplyState } from "../lib/types";
import { Select } from "./Select";
import { TimeWindows, describeWindows } from "./TimeWindows";
import { useConfirm } from "./ConfirmModal";

const GENERIC = ["通用", "全国", "未分组", ""];

interface Props {
  devices: Device[];
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
  onGoto: (view: "supply" | "content") => void;
}

/**
 * 自动发布。
 *
 * ⚠ 做这一页的起因：**线上 37 台的 `auto_publish` 全是 false，而全站没有任何
 * 界面能打开它**（`setDeviceAutoPublish` 在 api.ts 里定义了但零调用）。
 * 引擎、内容取件、每日篇数、时间段、错峰闸全都造好了，运营却够不着开关 ——
 * 所以到今天为止每一条发布都是人工点排期排出来的。
 *
 * 形态和「自动推送」页刻意一致（顶部数字 → 警示 → 今天 → 规则 → 账号），
 * 两条线学一次会两个。唯一的结构差别：群推送能预演「几点推哪一条」，
 * 发布这边**时刻是随机的、内容到点才从池子里取**，预告不了，
 * 所以「今天」这一段列的是每个账号的进度，并且如实说明这一点，不假装能预告。
 */
export function AutoPublishView({ devices, onNotice, onGoto }: Props) {
  const [roster, setRoster] = useState<{
    in_service: number;
    daily_target: number;
    today_target: number;
  }>();
  const [windows, setWindows] = useState("");
  const [savedWindows, setSavedWindows] = useState("");
  const [target, setTarget] = useState(1);
  const [supply, setSupply] = useState<SupplyState>();
  const [cityFilter, setCityFilter] = useState("all");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirm, confirmUI] = useConfirm();

  // ⚠ onNotice 是调用方内联写的箭头函数，每次渲染都是新的。写进 useCallback 的
  // 依赖里，这一页就会跟着 App 的 2 秒全局刷新变成轮询（自动推送页踩过这个坑）。
  const noticeRef = useRef(onNotice);
  noticeRef.current = onNotice;

  const reload = useCallback(async (quiet = false) => {
    try {
      const [r, w, t, s] = await Promise.all([
        getAccountRoster(),
        getPublishWindows(),
        getPublishTarget(),
        getSupply().catch(() => undefined),
      ]);
      setRoster(r);
      setSupply(s);
      setSavedWindows(w.windows);
      if (!quiet) {
        setWindows(w.windows);
        setTarget(t.daily_target);
      }
    } catch (error) {
      noticeRef.current(
        "error", "读取设置失败", error instanceof Error ? error.message : "",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 账号直接读 devices（全局刷新已经带着 auto_publish，免费且新鲜）
  const accounts = useMemo(
    () =>
      devices
        .flatMap((d) =>
          (d.accounts ?? []).map((a) => ({ device: d, account: a })),
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
    accounts.forEach(({ account }) => account.city && set.add(account.city));
    return [...set].filter((c) => !GENERIC.includes(c)).sort();
  }, [accounts]);

  const on = accounts.filter(({ account }) => account.auto_publish);
  const shown = accounts.filter(
    ({ account }) => cityFilter === "all" || account.city === cityFilter,
  );
  // 打开了自动发布、但这个号本身发不了
  const blockedOn = on.filter(
    ({ account }) => (account.health ?? "normal") !== "normal",
  );
  const todayDone = on.reduce(
    (sum, { account }) => sum + (account.today_published ?? 0),
    0,
  );
  // 每个号的实际期望 = min(全局目标, 该号上限)，和引擎、首页、企微群同一条规则
  const expectedFor = (quota: number) => Math.min(target, quota ?? target);
  const todayTarget = on.reduce(
    (sum, { account }) => sum + expectedFor(account.daily_quota),
    0,
  );
  const shortCities = (supply?.gaps ?? []).filter((g) => g.short > 0);
  const dirty = windows !== savedWindows;

  async function save() {
    setSaving(true);
    try {
      await savePublishWindows(windows);
      await setPublishTarget(target);
      await reload(true);
      setSavedWindows(windows);
      onNotice(
        "success",
        "已保存",
        windows.trim()
          ? `每个账号每天 ${target} 篇，时刻落在 ${describeWindows(windows)} 里随机。`
          : `每个账号每天 ${target} 篇。没有设时间段，手机空下来就发。`,
      );
    } catch (error) {
      onNotice("error", "保存失败", error instanceof Error ? error.message : "");
    } finally {
      setSaving(false);
    }
  }

  async function toggle(
    deviceId: number,
    nickname: string,
    next: boolean,
    quota: number,
    platform: string,
  ) {
    if (next) {
      const ok = await confirm({
        title: `打开 ${nickname} 的自动发布？`,
        detail:
          `打开后，这个账号每天会自动从内容库取 ${expectedFor(quota)} 篇发出去` +
          (windows.trim()
            ? `，时刻落在 ${describeWindows(windows)} 里随机。`
            : "。现在没有设时间段，手机空下来就会发。") +
          "\n\n这是真的往抖音发内容，不是预演。",
        confirmText: "打开",
      });
      if (!ok) return;
    }
    try {
      await setDeviceAutoPublish(deviceId, next, quota, platform);
      if (!next) {
        onNotice(
          "success",
          "已关闭",
          `${nickname} 不再自动发布。已经排进队列的那条还会发完，要立刻停就去执行记录里取消它。`,
        );
      }
    } catch (error) {
      onNotice("error", "操作失败", error instanceof Error ? error.message : "");
    }
  }

  async function stopAll() {
    const ok = await confirm({
      title: "关闭全部账号的自动发布？",
      danger: true,
      detail:
        `${on.length} 个账号会一起关掉，之后不再自动排新的。` +
        // ⚠ 这里是纯文本不是 Markdown —— 写 **强调** 会把星号原样打到屏幕上
        // （点出来的）。要强调就靠措辞和语序，别靠符号。
        "\n\n注意：已经排进队列、还没发出去的那些不会被取消。" +
        "要连它们一起停，去执行记录里逐条取消。",
      confirmText: "全部关闭",
    });
    if (!ok) return;
    let failed = 0;
    for (const { device, account } of on) {
      try {
        await setDeviceAutoPublish(
          device.id, false, account.daily_quota, account.platform,
        );
      } catch {
        failed += 1;
      }
    }
    onNotice(
      failed ? "error" : "success",
      failed ? "部分没关掉" : "已全部关闭",
      failed
        ? `${on.length - failed} 个已关闭，${failed} 个没成功，刷新后再试一次。`
        : `${on.length} 个账号的自动发布都关了。`,
    );
  }

  return (
    <div className="content-page page-enter">
      <header className="catalog-hero content-hero">
        <div>
          <span>内容发布</span>
          <h2>到点自己从内容库取一篇发出去。</h2>
          <p>
            打开的账号每天按设定的篇数自动发布，时刻在你选的时间段里随机。
            内容从内容库里按城市取，本城没有就用通用的，一条只会被一个账号发一次。
          </p>
        </div>
        <div className="hero-stats">
          <div className="hero-stat accent">
            <strong>
              {todayDone}
              <i style={{ fontSize: 16 }}> / {todayTarget || 0}</i>
            </strong>
            <small>今天已发</small>
          </div>
          <div className="hero-stat">
            <strong>{on.length}</strong>
            <small>参与的账号</small>
          </div>
          <div className="hero-stat">
            <strong>{target}</strong>
            <small>每个账号每天</small>
          </div>
        </div>
      </header>

      {/* 这些情况引擎全是静默跳过，别处一点异常都看不出来 */}
      <section>
        {!loading && !on.length ? (
          <p className="library-warn danger">
            还没有账号打开自动发布，所以现在每一条都得人工去排。
            在下面「哪些账号自动发」里打开需要的账号。
          </p>
        ) : null}
        {shortCities.length ? (
          <p className="library-warn">
            内容不够：
            {shortCities.map((g) => `${g.city} 还差 ${g.short} 篇`).join("、")}。
            取不到内容的账号今天会静静地少发几篇。
            <button className="button secondary" onClick={() => onGoto("supply")}>
              去自动生成
            </button>
          </p>
        ) : null}
        {blockedOn.length ? (
          <p className="library-warn">
            {blockedOn.map(({ account }) => account.nickname).join("、")}{" "}
            账号异常，虽然开着自动发布但不会发。处理好之后会自动恢复。
          </p>
        ) : null}
        {dirty ? (
          <p className="library-warn">
            时间段改了还没保存。点下面的「保存设置」才会生效。
          </p>
        ) : null}
      </section>

      {/* ── 今天 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>今天</span>
            <h2>每个账号发到哪儿了</h2>
          </div>
          <div className="record-filters">
            <small>
              具体几点发是随机的，预告不了；这里看的是已经发了几篇。
            </small>
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
            {on.length ? (
              <button className="button secondary danger-text" onClick={stopAll}>
                <Square size={13} /> 全部关闭
              </button>
            ) : null}
          </div>
        </header>

        {loading ? (
          <p className="content-block-empty">正在读取…</p>
        ) : !on.length ? (
          <p className="content-block-empty">
            还没有账号打开自动发布。打开之后，这里会显示每个账号今天发了几篇。
          </p>
        ) : (
          <table className="supply-table">
            <thead>
              <tr>
                <th>账号</th>
                <th>城市</th>
                <th>今天</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {on
                .filter(
                  ({ account }) =>
                    cityFilter === "all" || account.city === cityFilter,
                )
                .map(({ device, account }) => {
                  const want = expectedFor(account.daily_quota);
                  const done = account.today_published ?? 0;
                  const ill = (account.health ?? "normal") !== "normal";
                  return (
                    <tr key={account.id} className={ill ? "row-muted" : ""}>
                      <td>
                        {account.nickname || device.name}
                        <small className="tw-sep">{device.name}</small>
                      </td>
                      <td>
                        <span
                          className={`content-city ${
                            GENERIC.includes(account.city) ? "generic" : ""
                          }`}
                        >
                          {account.city || "未分组"}
                        </span>
                      </td>
                      <td className="num-cell">
                        {done} / {want}
                      </td>
                      <td>
                        <span
                          className={`slot-state ${
                            ill
                              ? "s-failed"
                              : done >= want
                                ? "s-sent"
                                : "s-upcoming"
                          }`}
                        >
                          {ill
                            ? "账号异常，不会发"
                            : done >= want
                              ? "今天发完了"
                              : `还差 ${want - done} 篇`}
                        </span>
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        )}

        <div className="supply-actions">
          <small>
            一台手机同一时刻只跑一条任务；两条发布之间至少隔 3 小时，
            所以同一个账号不会连着刷屏。
          </small>
        </div>
      </section>

      {/* ── 规则 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>规则</span>
            <h2>每天几篇，什么时候发</h2>
          </div>
        </header>

        <div className="supply-fields">
          <label className="field">
            <span>每个账号每天发几篇</span>
            <Select
              variant="field"
              value={String(target)}
              options={[1, 2, 3, 4, 5, 6].map((n) => ({
                value: String(n),
                label: `${n} 篇`,
              }))}
              onChange={(v) => setTarget(Number(v))}
            />
            <small className="field-note">
              首页的「今日已发」和企微群日报用的也是这个数。保存时会同步到每个账号，
              所以设几篇就是几篇。
            </small>
          </label>
        </div>

        <div className="field span-2">
          <span>发布时间段</span>
          <TimeWindows
            value={windows}
            onChange={setWindows}
            noun="发布"
            emptyHint="没有设时间段，手机一空下来就发。"
          />
          <small className="field-note">
            不设时间段也能用：手机空了就发下一篇。设了就只在这些时间段里发，
            同城的不同账号不会撞在同一分钟。
          </small>
        </div>

        <div className="supply-actions">
          <button className="button primary" disabled={saving} onClick={() => void save()}>
            <Check size={15} /> 保存设置
          </button>
          <small>
            现在设的是：每个账号每天 {target} 篇，
            {describeWindows(windows) || "不限时间段"}
          </small>
        </div>
      </section>

      {/* ── 哪些账号自动发 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>账号</span>
            <h2>哪些账号自动发</h2>
          </div>
          <small>
            没打开的账号不受影响，还是照旧人工排期，也可以继续用别的工具发。
          </small>
        </header>
        <p className="supply-hint">
          打开之后，这个账号每天按上面的设定，自己从内容库取内容发出去。
        </p>

        <div className="kv-rows">
          {!accounts.length ? (
            <p className="content-block-empty">还没有账号。先到账号总览点「检测设备」。</p>
          ) : (
            shown.map(({ device, account }) => {
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
                    <small>今天 {account.today_published ?? 0} 篇</small>
                    {(account.health ?? "normal") !== "normal" ? (
                      <span className="msg-chip off">账号异常</span>
                    ) : null}
                    {account.logged_in === false ? (
                      <span className="msg-chip off">未登录</span>
                    ) : null}
                  </span>
                  <button
                    className={`auto-toggle ${account.auto_publish ? "on" : ""}`}
                    onClick={() =>
                      void toggle(
                        device.id,
                        nickname,
                        !account.auto_publish,
                        account.daily_quota,
                        account.platform,
                      )
                    }
                  >
                    <span className="auto-toggle-track">
                      <i />
                    </span>
                    {account.auto_publish ? "自动发布" : "不参与"}
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
