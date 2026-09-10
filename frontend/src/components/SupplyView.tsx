import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  MapPin,
  Plus,
  RefreshCw,
  Trash2,
  Wand2,
} from "lucide-react";

import { getSupply, runSupply, saveSupply } from "../lib/api";
import type { Device, SupplyState } from "../lib/types";
import { Select } from "./Select";
import { useConfirm } from "./ConfirmModal";

/** 词表在库里是一个串：`上海:外滩|静安;北京:三里屯|国贸`。
 *  界面上必须是表格 —— 让运营去一个输入框里维护分隔符，迟早写坏，
 *  而写坏的后果是地名闸失效（那是对外可见的运营事故）。 */
interface TokenRow {
  city: string;
  words: string;
}

const GENERIC_CITY = ["通用", "全国", "未分组"];

const toRows = (table: Record<string, string[]>): TokenRow[] =>
  Object.entries(table).map(([city, words]) => ({
    city,
    words: words.join("、"),
  }));

const toRaw = (rows: TokenRow[]) =>
  rows
    .map((r) => ({
      city: r.city.trim(),
      // 顿号、逗号、空格都当分隔符 —— 运营从别处贴过来的词表什么分隔符都有
      words: r.words
        .split(/[、,，|\s]+/)
        .map((w) => w.trim())
        .filter(Boolean),
    }))
    .filter((r) => r.city && r.words.length)
    .map((r) => `${r.city}:${r.words.join("|")}`)
    .join(";");

function fmtTime(iso?: string | null) {
  if (!iso) return "还没生成过";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes(),
  ).padStart(2, "0")}`;
}

interface Props {
  devices: Device[];
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
}

/**
 * 内容自动生成。
 *
 * 这套东西后端早就跑得通（算缺口 → 生成 → 机器过闸 → 进池），但一直没有入口：
 * 开关、水位、单轮上限、每城地名词表都只能改库。所以「自动生成内容的闭环」
 * 在运营眼里等于不存在 —— 内容还是靠人记得去点「生成」。
 *
 * 页面上刻意分成三段，顺序就是运营的决策顺序：
 * ① 现在缺多少（不开也能看，看缺口不花钱）
 * ② 生成规则（水位/上限/样本门槛）
 * ③ 地名词表（开自动生成**之前**必须填，否则地名闸是空的）
 */
export function SupplyView({ devices, onNotice }: Props) {
  const [state, setState] = useState<SupplyState>();
  const [onCities, setOnCities] = useState<string[]>([]);
  const [minDays, setMinDays] = useState(3);
  const [batchCap, setBatchCap] = useState(10);
  const [floor, setFloor] = useState(120);
  const [rows, setRows] = useState<TokenRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [confirm, confirmUI] = useConfirm();

  function apply(next: SupplyState) {
    setState(next);
    setOnCities(next.enabled_cities ?? []);
    setMinDays(next.min_days);
    setBatchCap(next.batch_cap);
    setFloor(next.quality_floor);
    setRows(toRows(next.city_tokens));
  }

  useEffect(() => {
    getSupply()
      .then(apply)
      .catch((error) =>
        onNotice("error", "读取设置失败", error instanceof Error ? error.message : ""),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 在跑的号所在的城市，用来提示"这些城市还没有地名词表"
  const cities = useMemo(() => {
    const set = new Set<string>();
    devices.forEach((d) => {
      const c = (d.city || "").trim();
      if (c && !["通用", "全国", "未分组"].includes(c)) set.add(c);
    });
    return [...set].sort();
  }, [devices]);

  const covered = new Set(rows.map((r) => r.city.trim()).filter(Boolean));
  const missing = cities.filter((c) => !covered.has(c));

  async function save(cities = onCities) {
    setSaving(true);
    try {
      const next = await saveSupply({
        enabled: cities.length > 0,
        enabled_cities: cities,
        min_days: minDays,
        batch_cap: batchCap,
        quality_floor: floor,
        city_tokens_raw: toRaw(rows),
      });
      apply(next);
      onNotice(
        "success",
        "已保存",
        cities.length
          ? `${cities.join("、")} 已经开始生成：少于 ${next.min_days} 天的量时补上，` +
            `一次最多 ${next.batch_cap} 条。写好的内容会陆续进入内容库，` +
            `之后每发出去一篇都会自动补上。`
          : "没有城市开着自动生成，内容用完不会自动补上",
      );
    } catch (error) {
      onNotice("error", "保存失败", error instanceof Error ? error.message : "");
    } finally {
      setSaving(false);
    }
  }

  async function toggleCity(city: string, next: boolean) {
    // 打开 = 这个城市从此会自己花钱生成内容并直接进池。
    // 这个城市没填地名就更要拦一下：那意味着地名检查对它形同虚设。
    if (next) {
      const noWords = !covered.has(city) && !GENERIC_CITY.includes(city);
      const ok = await confirm({
        title: `${city} 开启自动生成？`,
        detail:
          `点「开启」就开始写第一批，最多 ${batchCap} 条，通过审核后进入内容库。` +
          `之后 ${city} 每发出去一篇都会自动补，库存不足 ${minDays} 天的量时补齐。` +
          (noWords
            ? `\n\n注意：${city} 还没有填地名，它的内容不会做地名检查，可能写出别处的地名。建议先到下面补上。`
            : ""),
        confirmText: "开启",
      });
      if (!ok) return;
    }
    const cities = next
      ? [...new Set([...onCities, city])]
      : onCities.filter((c) => c !== city);
    setOnCities(cities);
    await save(cities);
  }

  async function runNow() {
    const ok = await confirm({
      title: "现在生成一批？",
      detail:
        `按当前缺口生成，最多 ${batchCap} 条，通过审核后进入内容库。` +
        (onCities.length ? "" : "\n\n还没有城市开着自动生成，这一轮会被跳过。"),
      confirmText: "开始生成",
    });
    if (!ok) return;
    setRunning(true);
    try {
      const res = await runSupply();
      apply(res);
      const r = res.result;
      if (r.skipped) {
        onNotice("error", "没有执行", `这一轮被跳过：${r.skipped}`);
      } else if (!r.made) {
        onNotice("success", "暂时不用生成", "各城市的内容都够用");
      } else {
        onNotice(
          "success",
          `生成 ${r.made} 条，入库 ${r.accepted} 条`,
          r.rejected?.length
            ? `${r.rejected.length} 条没通过：${r.rejected.slice(0, 2).join("；")}`
            : "全部通过审核",
        );
      }
    } catch (error) {
      onNotice("error", "生成失败", error instanceof Error ? error.message : "");
    } finally {
      setRunning(false);
    }
  }

  const gaps = state?.gaps ?? [];
  // ⚠ 表格要列**所有有账号的城市**，不能只列有缺口的（city_gaps 只返回
  // have < need 的）。只列有缺口的话，库存够的城市就没法提前打开开关。
  const gapOf = new Map(gaps.map((g) => [g.city, g]));
  const cityRows = [
    ...new Set([...gaps.map((g) => g.city), ...cities, "通用"]),
  ].map((city) => ({ city, gap: gapOf.get(city) }));

  return (
    <div className="content-page page-enter">
      <header className="catalog-hero content-hero">
        <div>
          <span>内容</span>
          <h2>内容不够时，自动补上。</h2>
          <p>
            按城市算出还差多少，参考整个矩阵里表现好的作品来写，
            通过审核后进入内容库。城市决定内容写哪里，参考的样本按表现挑，
            不局限在本地。
          </p>
        </div>
        <div className="hero-stats">
          <div className={`hero-stat ${state?.short_total ? "accent" : ""}`}>
            <strong>{state?.short_total ?? "—"}</strong>
            <small>还差（篇）</small>
          </div>
          <div className="hero-stat">
            <strong>{onCities.length}</strong>
            <small>开着的城市</small>
          </div>
        </div>
      </header>

      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>当前状态</span>
            <h2>还差多少</h2>
          </div>
          <small>查看不会生成任何内容，也不产生费用</small>
        </header>
        {!cityRows.length ? (
          <p className="content-block-empty">还没有账号，先到账号总览点「检测设备」。</p>
        ) : (
          <table className="supply-table">
            <thead>
              <tr>
                <th>城市</th>
                <th>在跑的账号</th>
                <th>现有</th>
                <th>需要</th>
                <th>还差</th>
                <th>自动生成</th>
              </tr>
            </thead>
            <tbody>
              {cityRows.map(({ city, gap }) => (
                <tr key={city}>
                  <td>
                    <span
                      className={`content-city ${
                        GENERIC_CITY.includes(city) ? "generic" : ""
                      }`}
                    >
                      {city}
                    </span>
                  </td>
                  <td>{gap ? gap.accounts : "—"}</td>
                  <td>{gap ? gap.have : "—"}</td>
                  <td>{gap ? gap.need : "—"}</td>
                  {/* 够用的城市以前整行都是破折号 —— 而唯一真正在跑的那个城市
                      恰好一直是「够用」，于是页面上一个数字都看不到。
                      现在够用也给出「还够发几天」，压线和宽裕一眼能分开。 */}
                  <td>
                    {!gap ? (
                      "—"
                    ) : gap.short > 0 ? (
                      <b className="supply-short">{gap.short}</b>
                    ) : (
                      <span className="slot-state s-sent">
                        够用
                        {typeof gap.days_left === "number" ? (
                          <em className="supply-days">
                            还够发 {gap.days_left} 天
                          </em>
                        ) : null}
                      </span>
                    )}
                  </td>
                  <td>
                    <button
                      className={`auto-toggle ${
                        onCities.includes(city) ? "on" : ""
                      }`}
                      onClick={() =>
                        void toggleCity(city, !onCities.includes(city))
                      }
                    >
                      <span className="auto-toggle-track">
                        <i />
                      </span>
                      {onCities.includes(city) ? "开着" : "关着"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="supply-actions">
          <button className="button secondary" onClick={runNow} disabled={running}>
            <Wand2 size={15} /> {running ? "正在生成…" : "现在生成一批"}
          </button>
          <small>上次生成：{fmtTime(state?.last_run)}</small>
        </div>
      </section>

      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>规则</span>
            <h2>什么时候写，一次写多少</h2>
          </div>
        </header>

        <p className="supply-hint">
          {onCities.length
            ? `${onCities.join("、")} 开着自动生成，下面这几条规则对它们都生效。`
            : "还没有城市开着自动生成。在上面那张表里逐个城市打开 —— 各城市的库存和投放节奏不一样，不必一起开。"}
        </p>

        <div className="supply-fields">
          <label className="field">
            <span>少于几天的量就生成</span>
            <Select
              variant="field"
              value={String(minDays)}
              options={[2, 3, 5, 7, 10, 14].map((n) => ({
                value: String(n),
                label: `${n} 天`,
              }))}
              onChange={(v) => setMinDays(Number(v))}
            />
            <small className="field-note">
              按「在跑的账号 × 每个账号每天的篇数」折算。够用就不写，
              免得内容堆到发不完。
            </small>
          </label>
          <label className="field">
            <span>一次最多生成</span>
            <Select
              variant="field"
              value={String(batchCap)}
              options={[5, 10, 15, 20, 30].map((n) => ({
                value: String(n),
                label: `${n} 条`,
              }))}
              onChange={(v) => setBatchCap(Number(v))}
            />
            <small className="field-note">
              一次算出的缺口再大，也不会超过这个数。
            </small>
          </label>
          <label className="field">
            <span>参考样本的门槛</span>
            <Select
              variant="field"
              value={String(floor)}
              options={[0, 60, 120, 200, 400].map((n) => ({
                value: String(n),
                label: n === 0 ? "不设限" : `互动 ≥ ${n}`,
              }))}
              onChange={(v) => setFloor(Number(v))}
            />
            <small className="field-note">
              本地作品达不到这个互动量，就改用整个矩阵里表现更好的作为参考。
            </small>
          </label>
        </div>
      </section>

      <section className="supply-block">
        <header className="section-heading">
          <div>
            <span>安全</span>
            <h2>各城市的地名</h2>
          </div>
          <small>开启自动生成前请先填好</small>
        </header>
        <p className="supply-hint">
          参考样本来自各个城市，写的时候可能把别处的地名带进来 ——
          比如北京的账号发出一篇写上海商圈的文案。
          在这里逐个城市列出地名，正文里出现了别的城市的词，这篇就不会进内容库。
        </p>

        {missing.length ? (
          <p className="library-warn">
            <AlertTriangle size={13} /> {missing.join("、")}{" "}
            还没有填地名，这些城市的内容不会做这项检查。
          </p>
        ) : null}

        <div className="kv-rows">
          {rows.map((row, i) => (
            <div className="kv-row" key={i}>
              <input
                className="token-city"
                value={row.city}
                placeholder="城市"
                onChange={(e) =>
                  setRows(rows.map((r, j) => (i === j ? { ...r, city: e.target.value } : r)))
                }
              />
              <input
                className="token-words"
                value={row.words}
                placeholder="该城市的地名，用顿号或逗号分开：外滩、静安、陆家嘴"
                onChange={(e) =>
                  setRows(rows.map((r, j) => (i === j ? { ...r, words: e.target.value } : r)))
                }
              />
              <button
                className="ghost-icon danger"
                title="删掉这一行"
                onClick={() => setRows(rows.filter((_, j) => j !== i))}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          <div className="supply-actions">
            <button
              className="button secondary"
              onClick={() => setRows([...rows, { city: missing[0] ?? "", words: "" }])}
            >
              <Plus size={15} /> 加一个城市
            </button>
            {missing.length ? (
              <small>
                <MapPin size={11} /> 还没填地名：{missing.join("、")}
              </small>
            ) : null}
          </div>
        </div>
      </section>

      <div className="supply-save">
        <button className="button primary" onClick={() => void save()} disabled={saving}>
          {saving ? <RefreshCw size={15} /> : <Check size={15} />} 保存设置
        </button>
      </div>
      {confirmUI}
    </div>
  );
}
