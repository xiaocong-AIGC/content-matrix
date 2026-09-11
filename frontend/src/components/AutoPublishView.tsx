import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, RefreshCw, Square } from "lucide-react";

import {
  getAccountRoster,
  getPublishCities,
  getPublishTarget,
  getPublishWindows,
  getSupply,
  putPublishCity,
  savePublishWindows,
  setDeviceAutoPublish,
  setPublishOverride,
  setPublishTarget,
} from "../lib/api";
import type { CityPolicy, Device, DeviceAccount, SupplyState } from "../lib/types";
import { Select } from "./Select";
import { TimeWindows, describeWindows } from "./TimeWindows";
import { useConfirm } from "./ConfirmModal";

const GENERIC = ["通用", "全国", "未分组", ""];

interface Props {
  devices: Device[];
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
  onGoto: (view: "supply" | "content") => void;
}

/** 实际发不发。后端下发的 auto_publish 就是实际值（publishes 是同一个值的新名字）。 */
const effective = (a: DeviceAccount) => a.publishes ?? a.auto_publish;

/**
 * 自动发布。
 *
 * **城市是主单位，账号是例外。** 每个城市单独设开关、每天几篇、什么时候发；
 * 个别号要不一样，在下面单独设。城市里新加的号自动跟着城市走。
 *
 * ⚠ 城市开关只有「发 / 不发」两档，**没有「各号自己定」**。审查时抓到它是个
 * 陷阱：选它会退回到每个号的老开关 —— 这个值界面上看不见、也改不了；深圳当时
 * 是「不发」而 5 个号的老开关都是开的，选「各号自己定」会**不弹任何确认就让
 * 5 个号立刻开始真发**。没设过开关的城市显示「还没按城市设」，只能往发/不发走，
 * 不能退回去。
 *
 * 形态和「自动推送」页刻意一致（顶部数字 → 警示 → 今天 → 规则 → 账号）。
 * 发布这边**时刻是随机的、内容到点才从池子里取**，预告不了，
 * 所以「今天」列的是每个号的进度，并且如实说明这一点。
 */
export function AutoPublishView({ devices, onNotice, onGoto }: Props) {
  const [windows, setWindows] = useState("");
  const [savedWindows, setSavedWindows] = useState("");
  const [target, setTarget] = useState(1);
  const [savedTarget, setSavedTarget] = useState(1);
  const [supply, setSupply] = useState<SupplyState>();
  const [cities, setCities] = useState<CityPolicy[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftWindows, setDraftWindows] = useState("");
  const [cityFilter, setCityFilter] = useState("all");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  // 设置有没有真的读到。没读到时默认区停在「不限时段、1 篇」的初值 ——
  // 这时候点保存会把全局设置覆盖掉，所以没读到就不许保存。
  const [loaded, setLoaded] = useState(false);
  const [confirm, confirmUI] = useConfirm();

  // ⚠ onNotice 是调用方内联写的箭头函数，每次渲染都是新的。写进 useCallback 的
  // 依赖里，这一页就会跟着 App 的 2 秒全局刷新变成轮询（自动推送页踩过这个坑）。
  const noticeRef = useRef(onNotice);
  noticeRef.current = onNotice;

  const reload = useCallback(async (quiet = false) => {
    try {
      const [, w, t, s, c] = await Promise.all([
        getAccountRoster(),
        getPublishWindows(),
        getPublishTarget(),
        getSupply().catch(() => undefined),
        getPublishCities(),
      ]);
      setSupply(s);
      setCities(c.cities);
      setSavedWindows(w.windows);
      setSavedTarget(t.daily_target);
      if (!quiet) {
        setWindows(w.windows);
        setTarget(t.daily_target);
      }
      setLoaded(true);
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

  // 只刷城市那一块。账号例外改了之后，城市卡片上的「几个在发 / 几个单独设了」要跟着变 ——
  // 设备列表有全局 2 秒轮询、城市列表没有，不刷就是顶部说 4 个在发、卡片说 3 个。
  const refreshCities = useCallback(async () => {
    try {
      setCities((await getPublishCities()).cities);
    } catch {
      // 拉不到就留着旧的，下一次 reload 会补上
    }
  }, []);

  const accounts = useMemo(
    () =>
      devices
        .flatMap((d) => (d.accounts ?? []).map((a) => ({ device: d, account: a })))
        .sort((x, y) =>
          `${x.account.city}${x.account.nickname}`.localeCompare(
            `${y.account.city}${y.account.nickname}`,
            "zh",
          ),
        ),
    [devices],
  );

  const on = accounts.filter(({ account }) => effective(account));
  const cityOf = (name: string) => cities.find((c) => c.city === name);
  // 每个号今天该发几篇：**用后端算好的**。前端自己拿城市篇数、全局篇数、上限去组合，
  // 迟早和引擎不一致。老后端没有 target 就退回「已保存的」全局篇数 —— 不能用下拉里
  // 还没保存的那个值，否则改了下拉不点保存，今天表就开始显示一个引擎没用的数。
  const wantFor = (a: DeviceAccount) =>
    a.target ?? Math.min(savedTarget, a.daily_quota ?? savedTarget);
  const blockedOn = on.filter(({ account }) => (account.health ?? "normal") !== "normal");
  const todayDone = on.reduce((sum, { account }) => sum + (account.today_published ?? 0), 0);
  const todayTarget = on.reduce((sum, { account }) => sum + wantFor(account), 0);
  const shortCities = (supply?.gaps ?? []).filter((g) => g.short > 0);
  const dirty = windows !== savedWindows || target !== savedTarget;
  // 没设城市的号没有城市可跟，只能一个个开 —— 单独列出来提醒去补城市
  const homeless = accounts.filter(({ account }) => GENERIC.includes(account.city));

  async function saveDefaults() {
    setSaving(true);
    try {
      await savePublishWindows(windows);
      // 篇数没改就别调这个接口 —— 它会把所有号的上限覆盖成全局值
      if (target !== savedTarget) await setPublishTarget(target);
      await reload(true);
      onNotice("success", "默认设置已保存", "没有单独设置的城市会按这个来。");
    } catch (error) {
      onNotice("error", "保存失败", error instanceof Error ? error.message : "");
    } finally {
      setSaving(false);
    }
  }

  /** 成功返回 true。失败只弹提示、返回 false —— 调用方靠它决定要不要保持编辑态。 */
  async function patchCity(
    city: string,
    patch: Parameters<typeof putPublishCity>[1],
    done?: string,
  ): Promise<boolean> {
    try {
      const res = await putPublishCity(city, patch);
      setCities(res.cities);
      if (done) onNotice("success", done, "");
      return true;
    } catch (error) {
      onNotice("error", "没保存上", error instanceof Error ? error.message : "");
      return false;
    }
  }

  async function setCitySwitch(c: CityPolicy, value: string) {
    if (value !== "on" && value !== "off") return; // 「还没按城市设」只显示、不能选回去
    const next = value === "on";
    const cityAccounts = accounts.filter(({ account }) => account.city === c.city);
    if (next) {
      const perHead = c.effective_target;
      const skipped = c.overrides_off;
      const ok = await confirm({
        title: `打开${c.city}的自动发布？`,
        detail:
          `${c.city}的号每天会自动从内容库取内容发出去，每个号最多 ${perHead} 篇` +
          (c.effective_unrestricted
            ? "。现在没有设时间段，手机空下来就会发。"
            : `，时刻落在 ${c.effective_windows} 里随机。`) +
          (skipped ? `\n\n其中 ${skipped} 个号单独设了不发，它们还是不发。` : "") +
          "\n\n以后在这个城市新加的号会自动跟着发，不用再一个个开。" +
          "\n\n这是真的往抖音发内容，不是预演。",
        confirmText: "打开",
      });
      if (!ok) return;
    }
    const saved = await patchCity(c.city, { auto_publish: next });
    if (!saved) return;
    // 关城市时，单独设了「发」的号还在发 —— 必须说出来，否则运营以为全城都停了
    const stillOn = cityAccounts.filter(
      ({ account }) => account.publish_override === "on",
    ).length;
    onNotice(
      "success",
      next ? `${c.city}已打开` : `${c.city}已关闭`,
      !next && stillOn ? `有 ${stillOn} 个号单独设了发，它们还会继续发。` : "",
    );
  }

  async function setOverride(deviceId: number, nickname: string, value: string) {
    const next = value === "on" ? "on" : value === "off" ? "off" : null;
    if (next === "on") {
      const ok = await confirm({
        title: `让 ${nickname} 单独发？`,
        detail: "不管这个号所在的城市开没开，它都会每天自动发。\n\n这是真的往抖音发内容，不是预演。",
        confirmText: "单独发",
      });
      if (!ok) return;
    }
    try {
      await setPublishOverride(deviceId, next);
      await refreshCities();
      onNotice(
        "success",
        next === null ? `${nickname} 改回跟着城市` : `${nickname} 已单独设置`,
        "",
      );
    } catch (error) {
      onNotice("error", "操作失败", error instanceof Error ? error.message : "");
    }
  }

  async function toggleHomeless(account: DeviceAccount, deviceId: number, nickname: string) {
    const next = !effective(account);
    if (next) {
      const ok = await confirm({
        title: `打开 ${nickname} 的自动发布？`,
        detail:
          "这个号还没设城市，所以只能单独打开。设了城市之后它就跟着城市走。" +
          "\n\n这是真的往抖音发内容，不是预演。",
        confirmText: "打开",
      });
      if (!ok) return;
    }
    try {
      await setDeviceAutoPublish(deviceId, next, account.daily_quota, account.platform);
      await refreshCities();
    } catch (error) {
      onNotice("error", "操作失败", error instanceof Error ? error.message : "");
    }
  }

  async function stopAll() {
    const targets = [...on];
    const ok = await confirm({
      title: "关闭全部自动发布？",
      danger: true,
      detail:
        `所有城市都会关掉，单独开着的号也一起关，一共 ${targets.length} 个号不再自动排新的。` +
        // ⚠ 这里是纯文本不是 Markdown —— 写 **强调** 会把星号原样打到屏幕上
        "\n\n注意：已经排进队列、还没发出去的那些不会被取消。" +
        "要连它们一起停，去执行记录里逐条取消。",
      confirmText: "全部关闭",
    });
    if (!ok) return;
    let failed = 0;
    // ⚠ 顺序不能反：先关城市，再逐号关。
    // 先关号的话城市还开着，每个号会被记成一条永久的「单独不发」例外 ——
    // 之后再开城市，这些号还是不发，而运营只是想临时全停一下。
    // 逐号那一步走老开关接口：城市有开关时它会清掉例外（和城市一致），
    // 城市没开关时它关掉老开关并清掉例外。一条接口覆盖了所有让号在发的路径。
    for (const c of cities.filter((x) => x.auto_publish === true)) {
      try {
        await putPublishCity(c.city, { auto_publish: false });
      } catch {
        failed += 1;
      }
    }
    for (const { device, account } of targets) {
      try {
        await setDeviceAutoPublish(device.id, false, account.daily_quota, account.platform);
      } catch {
        failed += 1;
      }
    }
    await reload(true);
    onNotice(
      failed ? "error" : "success",
      failed ? `有 ${failed} 处没关掉` : "已全部关闭",
      failed ? "刷新之后再点一次。" : `${targets.length} 个号都不会再自动排新的了。`,
    );
  }

  async function saveCityWindows(c: CityPolicy) {
    // 草稿和默认一模一样就别存成城市自己的 —— 存进去之后这个城市就和默认脱钩了，
    // 以后改默认时段它不再跟着变，而界面上只是少了「跟默认」三个字。
    const same = draftWindows.trim() === savedWindows.trim();
    const ok = await patchCity(
      c.city,
      { windows: same ? null : draftWindows },
      same ? `${c.city}和默认一样，改成跟默认` : `${c.city}的时间段已保存`,
    );
    if (ok) setEditing(null); // 没保存上就留在编辑态，调好的时段别丢
  }

  const citiesInView = cities.filter((c) => c.accounts > 0);

  return (
    <div className="content-page page-enter">
      <header className="catalog-hero content-hero">
        <div>
          <span>内容发布</span>
          <h2>到点自己从内容库取一篇发出去。</h2>
          <p>
            按城市打开，每个城市单独设每天几篇、什么时候发。
            内容从内容库里按城市取，本城没有就用通用的，一条只会被一个号发一次。
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
            <small>在发的号</small>
          </div>
          <div className="hero-stat">
            <strong>{citiesInView.filter((c) => c.publishing > 0).length}</strong>
            <small>在发的城市</small>
          </div>
        </div>
      </header>

      {/* 这些情况引擎全是静默跳过，别处一点异常都看不出来 */}
      <section>
        {!loading && !on.length ? (
          <p className="library-warn danger">
            还没有城市打开自动发布，所以现在每一条都得人工去排。在下面按城市打开。
          </p>
        ) : null}
        {shortCities.length ? (
          <p className="library-warn">
            内容不够：
            {shortCities.map((g) => `${g.city} 还差 ${g.short} 篇`).join("、")}。
            取不到内容的号今天会少发几篇。
            <button className="button secondary" onClick={() => onGoto("supply")}>
              去自动生成
            </button>
          </p>
        ) : null}
        {blockedOn.length ? (
          <p className="library-warn">
            {blockedOn.map(({ account }) => account.nickname).join("、")}{" "}
            账号异常，这几个号今天不会发。处理好之后会自动恢复。
          </p>
        ) : null}
      </section>

      {/* ── 今天 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>今天</span>
            <h2>每个号发到哪儿了</h2>
          </div>
          <div className="record-filters">
            <small>具体几点发是随机的，预告不了；这里看的是已经发了几篇。</small>
            {citiesInView.length ? (
              <Select
                label="城市"
                value={cityFilter}
                options={[
                  { value: "all", label: "全部城市" },
                  ...citiesInView.map((c) => ({ value: c.city, label: c.city })),
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
            还没有号在自动发布。打开一个城市之后，这里会显示每个号今天发了几篇。
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
                .filter(({ account }) => cityFilter === "all" || account.city === cityFilter)
                .map(({ device, account }) => {
                  const want = wantFor(account);
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
                            ill ? "s-failed" : done >= want ? "s-sent" : "s-upcoming"
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
            所以同一个号不会连着刷屏。
          </small>
        </div>
      </section>

      {/* ── 按城市 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>城市</span>
            <h2>哪些城市自动发，每个城市怎么发</h2>
          </div>
          <small>城市里新加的号会自动跟着城市走，不用再一个个开。</small>
        </header>

        {!citiesInView.length ? (
          <p className="content-block-empty">
            还没有设了城市的号。先在账号总览里给号设上城市。
          </p>
        ) : (
          <div className="kv-rows">
            {citiesInView.map((c) => {
              const unset = c.auto_publish === null;
              return (
                <div className="city-policy" key={c.city}>
                  <div className="kv-row account-row">
                    <span className="account-name">{c.city}</span>
                    <span className="account-meta">
                      <small>{c.accounts} 个号</small>
                      <small>
                        {c.publishing} 个在发
                        {c.publishing ? `，今天一共 ${c.publishing_target} 篇` : ""}
                      </small>
                      {c.overrides_on ? (
                        <span className="msg-chip">{c.overrides_on} 个号单独发</span>
                      ) : null}
                      {c.overrides_off ? (
                        <span className="msg-chip">{c.overrides_off} 个号单独不发</span>
                      ) : null}
                    </span>
                    <Select
                      value={unset ? "unset" : c.auto_publish ? "on" : "off"}
                      options={[
                        ...(unset
                          ? [{ value: "unset", label: "还没按城市设" }]
                          : []),
                        { value: "on", label: "自动发布" },
                        { value: "off", label: "不发" },
                      ]}
                      onChange={(v) => void setCitySwitch(c, v)}
                    />
                  </div>
                  <div className="city-policy-rules">
                    <label>
                      <span>每天几篇</span>
                      <Select
                        value={c.daily_target === null ? "default" : String(c.daily_target)}
                        options={[
                          { value: "default", label: `跟默认（${savedTarget} 篇）` },
                          ...[1, 2, 3, 4, 5, 6].map((n) => ({
                            value: String(n),
                            label: `${n} 篇`,
                          })),
                        ]}
                        onChange={(v) =>
                          void patchCity(c.city, {
                            daily_target: v === "default" ? null : Number(v),
                          })
                        }
                      />
                    </label>
                    <div className="city-policy-windows">
                      <span>什么时候发</span>
                      {editing === c.city ? (
                        <>
                          <TimeWindows
                            value={draftWindows}
                            onChange={setDraftWindows}
                            noun="发布"
                            emptyHint="不设时间段，手机一空下来就发。"
                          />
                          <div className="city-policy-actions">
                            <button
                              className="button primary"
                              onClick={() => void saveCityWindows(c)}
                            >
                              <Check size={14} /> 保存
                            </button>
                            <button
                              className="button secondary"
                              onClick={() => setEditing(null)}
                            >
                              取消
                            </button>
                          </div>
                        </>
                      ) : (
                        <p>
                          <b>{c.effective_unrestricted ? "不限时间段" : c.effective_windows}</b>
                          {c.windows === null ? <em>跟默认</em> : null}
                          <button
                            className="linklike"
                            onClick={() => {
                              setDraftWindows(c.windows ?? savedWindows);
                              setEditing(c.city);
                            }}
                          >
                            单独设
                          </button>
                          {c.windows !== null ? (
                            <button
                              className="linklike"
                              onClick={() =>
                                void patchCity(c.city, { windows: null }, `${c.city}改回跟默认`)
                              }
                            >
                              改回跟默认
                            </button>
                          ) : null}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── 默认设置 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>默认</span>
            <h2>没单独设的城市按这个来</h2>
          </div>
        </header>

        <div className="supply-fields">
          <label className="field">
            <span>每个号每天发几篇</span>
            <Select
              variant="field"
              value={String(target)}
              options={[1, 2, 3, 4, 5, 6].map((n) => ({ value: String(n), label: `${n} 篇` }))}
              onChange={(v) => setTarget(Number(v))}
            />
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
            设了就只在这些时间段里发，同城的不同号不会撞在同一分钟。
          </small>
        </div>

        <div className="supply-actions">
          <button
            className="button primary"
            disabled={saving || !loaded}
            onClick={() => void saveDefaults()}
          >
            <Check size={15} /> 保存默认设置
          </button>
          {!loaded && !loading ? (
            <small>设置没读到，先刷新一下，免得把默认设置覆盖掉。</small>
          ) : dirty ? (
            <small>改了还没保存。</small>
          ) : (
            <small>
              现在的默认：每个号每天 {savedTarget} 篇，
              {describeWindows(savedWindows) || "不限时间段"}
            </small>
          )}
        </div>
      </section>

      {/* ── 单个号 ── */}
      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>单个号</span>
            <h2>个别号要跟城市不一样</h2>
          </div>
          <small>比如全城开着，这个号今天被限流了，先单独关它。</small>
        </header>

        <div className="kv-rows">
          {!accounts.length ? (
            <p className="content-block-empty">还没有账号。先到账号总览点「检测设备」。</p>
          ) : (
            accounts
              .filter(({ account }) => !GENERIC.includes(account.city))
              .map(({ device, account }) => {
                const nickname = account.nickname || device.name;
                const follow = cityOf(account.city);
                const now = effective(account) ? "在发" : "不发";
                // 「跟城市」后面直接写这个号**现在**发不发，而不是城市的设置 ——
                // 城市还没设开关时，号靠的是自己原来的开关，只写「跟城市」看不出发不发。
                const followLabel =
                  follow?.auto_publish === null || !follow
                    ? `还没按城市设（现在${now}）`
                    : `跟城市（${now}）`;
                return (
                  <div className="kv-row account-row" key={account.id}>
                    <span className="account-name">{nickname}</span>
                    <span className="account-meta">
                      <span className="content-city">{account.city}</span>
                      <small>{device.name}</small>
                      <small>今天 {account.today_published ?? 0} 篇</small>
                      {(account.health ?? "normal") !== "normal" ? (
                        <span className="msg-chip off">账号异常</span>
                      ) : null}
                      {account.logged_in === false ? (
                        <span className="msg-chip off">未登录</span>
                      ) : null}
                    </span>
                    <Select
                      value={account.publish_override ?? "follow"}
                      options={[
                        { value: "follow", label: followLabel },
                        { value: "on", label: "这个号单独发" },
                        { value: "off", label: "这个号单独不发" },
                      ]}
                      onChange={(v) => void setOverride(device.id, nickname, v)}
                    />
                  </div>
                );
              })
          )}
        </div>

        {homeless.length ? (
          <>
            <p className="supply-hint">
              下面这几个号还没设城市，没有城市可跟，只能一个个开。在账号总览给它们设上城市会省事。
            </p>
            <div className="kv-rows">
              {homeless.map(({ device, account }) => {
                const nickname = account.nickname || device.name;
                const isOn = effective(account);
                return (
                  <div className="kv-row account-row" key={account.id}>
                    <span className="account-name">{nickname}</span>
                    <span className="account-meta">
                      <span className="content-city generic">未分组</span>
                      <small>{device.name}</small>
                    </span>
                    <button
                      className={`auto-toggle ${isOn ? "on" : ""}`}
                      onClick={() => void toggleHomeless(account, device.id, nickname)}
                    >
                      <span className="auto-toggle-track">
                        <i />
                      </span>
                      {isOn ? "自动发布" : "不参与"}
                    </button>
                  </div>
                );
              })}
            </div>
          </>
        ) : null}
      </section>
      {confirmUI}
    </div>
  );
}
