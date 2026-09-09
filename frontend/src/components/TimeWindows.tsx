import { Plus, Trash2 } from "lucide-react";

import { Select } from "./Select";

export interface TimeWindow {
  start: string; // "HH:MM"
  end: string;
}

const pad = (n: number) => String(n).padStart(2, "0");
const HOURS = Array.from({ length: 24 }, (_, i) => pad(i));
// 和 DateTime24 的分钟粒度完全一致 —— 不新造一套刻度
const MINUTES = Array.from({ length: 12 }, (_, i) => pad(i * 5));

const toMin = (t: string) => {
  const [h, m] = t.split(":");
  return Number(h) * 60 + Number(m || 0);
};
const fromMin = (v: number) => `${pad(Math.floor(v / 60))}:${pad(v % 60)}`;

/**
 * 宽松解析，给编辑器用：**保留 end <= start 的行**。
 *
 * 后端 `parse_windows` 对这种行是整段跳过、不报错 —— 那是排程该有的容错
 * （一个填错的值不该把发布全停）。但编辑器照做的话，用户会看到自己刚填的那行
 * 凭空消失。这里留着它，让页面能把问题说出来。
 */
export function parseWindows(raw: string): TimeWindow[] {
  const out: TimeWindow[] = [];
  for (const chunk of (raw ?? "").split(",")) {
    const text = chunk.trim();
    if (!text.includes("-")) continue;
    const [head, tail] = text.split("-");
    const norm = (part: string) => {
      const [h, m] = part.trim().split(":");
      const hh = Number(h);
      const mm = Number(m ?? 0);
      if (!Number.isFinite(hh) || hh < 0 || hh > 23) return null;
      if (!Number.isFinite(mm) || mm < 0 || mm > 59) return null;
      return `${pad(hh)}:${pad(mm)}`;
    };
    const start = norm(head);
    const end = norm(tail);
    if (start && end) out.push({ start, end });
  }
  return out;
}

export function formatWindows(rows: TimeWindow[]): string {
  return rows.map((r) => `${r.start}-${r.end}`).join(",");
}

/** 和后端 describe() 同款顿号分隔，两边说同一句话。 */
export function describeWindows(raw: string): string {
  return parseWindows(raw)
    .filter((r) => toMin(r.end) > toMin(r.start))
    .map((r) => `${r.start}-${r.end}`)
    .join("、");
}

/** 页面上要说出来的问题。空数组 = 没问题。 */
export function windowIssues(rows: TimeWindow[], count?: number): string[] {
  const notes: string[] = [];
  const valid = rows.filter((r) => toMin(r.end) > toMin(r.start));
  if (rows.length && !valid.length) {
    notes.push("现在这些时间段都不成立，保存了也不会执行。");
  }
  // 重叠只提醒不拦：后端允许，但重叠通常是手误
  for (let i = 0; i < valid.length; i += 1) {
    for (let j = i + 1; j < valid.length; j += 1) {
      const a = valid[i];
      const b = valid[j];
      if (toMin(a.start) < toMin(b.end) && toMin(b.start) < toMin(a.end)) {
        notes.push(
          `${a.start}-${a.end} 和 ${b.start}-${b.end} 有重叠，这两段里的时刻可能挨得很近。`,
        );
      }
    }
  }
  if (count && valid.length) {
    if (count === valid.length) {
      notes.push(
        `每天 ${count} 次，各落在 ${valid
          .map((r) => `${r.start}-${r.end}`)
          .join("、")} 里的一个随机时刻。`,
      );
    } else if (count > valid.length) {
      // plan_times 是 windows[index % len]，3 次 2 段 = 第一段两次 ——
      // 界面上完全看不出来，不写出来运营不会预料到同一个小时里推两条
      const per = valid.map((_, i) => {
        let n = 0;
        for (let k = 0; k < count; k += 1) if (k % valid.length === i) n += 1;
        return n;
      });
      notes.push(
        `每天 ${count} 次：` +
          valid.map((r, i) => `${r.start}-${r.end} 里 ${per[i]} 次`).join("，") +
          "。",
      );
    } else {
      notes.push(
        `每天 ${count} 次，只会用到前 ${count} 段，` +
          valid
            .slice(count)
            .map((r) => `${r.start}-${r.end}`)
            .join("、") +
          " 用不上。",
      );
    }
  }
  return notes;
}

/**
 * 时间段编辑器。群推送和自动发布共用 ——
 * 用户对两边提的是同一条要求：「时间可以在规定的时间内随机，
 * 比如我选时间为：10-11，14-15……发布也是」。
 *
 * 值就是后端存的那个串，两个调用方原样透传，解析只在这里发生一次。
 *
 * 两条刻意的行为：
 *
 * **① 永远不吐出后端会丢掉的串。** `parse_windows` 对 `end <= start` 是整段
 * 跳过、不报错 —— 运营存了一个看着生效的设置，实际一次都不推，而且毫无征兆。
 * 所以改开始时间越过结束时，把结束顶开；反之亦然。
 *
 * **② 进来的值不在 5 分钟刻度上（历史值 10:15）就把它并进选项里照常显示，
 * 不改写、不 onChange。** 页面一加载就静默改用户的数据、把页面推进脏状态，
 * 比"刻度不整齐"糟得多 —— 一次误保存就写坏线上设置。
 */
export function TimeWindows({
  value,
  onChange,
  count,
  noun = "推送",
  emptyHint,
  max = 6,
  disabled = false,
}: {
  /** 后端原样的串："10:00-11:00,14:00-15:00"；空串 = 没有时间段 */
  value: string;
  onChange: (raw: string) => void;
  /** 每天几次。只用来渲染「几次落在哪段」的说明，不参与取值 */
  count?: number;
  /** 进说明文案的动词 */
  noun?: string;
  /** 一段都没有时显示什么。两边语义相反，必须由调用方给 */
  emptyHint?: string;
  max?: number;
  disabled?: boolean;
}) {
  const rows = parseWindows(value);

  function push(next: TimeWindow[]) {
    onChange(formatWindows(next));
  }

  function setPart(i: number, key: "start" | "end", part: "h" | "m", v: string) {
    const next = rows.map((r) => ({ ...r }));
    const cur = next[i][key];
    const [h, m] = cur.split(":");
    next[i][key] = part === "h" ? `${v}:${m}` : `${h}:${v}`;
    // 不让它产出一个后端会静默丢掉的行
    if (toMin(next[i].end) <= toMin(next[i].start)) {
      if (key === "start") {
        next[i].end = fromMin(Math.min(toMin(next[i].start) + 60, 23 * 60 + 55));
        if (toMin(next[i].end) <= toMin(next[i].start)) {
          next[i].start = fromMin(toMin(next[i].end) - 5);
        }
      } else {
        next[i].start = fromMin(Math.max(toMin(next[i].end) - 60, 0));
      }
    }
    push(next);
  }

  /** 历史值可能不在 5 分钟刻度上，把它并进来照常显示 */
  function minuteOptions(current: string) {
    const mm = current.split(":")[1] ?? "00";
    const list = MINUTES.includes(mm) ? MINUTES : [...MINUTES, mm].sort();
    return list.map((v) => ({ value: v, label: v }));
  }

  const notes = windowIssues(rows, count);

  return (
    <div className="tw-wrap">
      <div className="tw-rows">
        {rows.map((row, i) => (
          <div className="tw-row" key={i}>
            <Select
              value={row.start.split(":")[0]}
              options={HOURS.map((v) => ({ value: v, label: v }))}
              onChange={(v) => setPart(i, "start", "h", v)}
              disabled={disabled}
            />
            <Select
              value={row.start.split(":")[1]}
              options={minuteOptions(row.start)}
              onChange={(v) => setPart(i, "start", "m", v)}
              disabled={disabled}
            />
            <span className="tw-sep">到</span>
            <Select
              value={row.end.split(":")[0]}
              options={HOURS.map((v) => ({ value: v, label: v }))}
              onChange={(v) => setPart(i, "end", "h", v)}
              disabled={disabled}
            />
            <Select
              value={row.end.split(":")[1]}
              options={minuteOptions(row.end)}
              onChange={(v) => setPart(i, "end", "m", v)}
              disabled={disabled}
            />
            <span />
            <button
              className="ghost-icon danger"
              title="删掉这个时间段"
              disabled={disabled}
              onClick={() => push(rows.filter((_, j) => j !== i))}
            >
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        {!rows.length && emptyHint ? (
          <p className="field-note">{emptyHint}</p>
        ) : null}
      </div>

      <div className="supply-actions">
        <button
          className="button secondary"
          disabled={disabled || rows.length >= max}
          onClick={() => push([...rows, { start: "10:00", end: "11:00" }])}
        >
          <Plus size={15} /> 加一个时间段
        </button>
        {rows.length >= max ? (
          <small>最多 {max} 个时间段。</small>
        ) : null}
      </div>

      {/* 跨零点后端直接丢弃，说明必须常驻，不能等用户撞上才说 */}
      <p className="field-note">
        一个时间段要在同一天之内，跨过零点的时段设不了。每天的具体
        {noun}时刻在段内随机，落点每天不一样。
      </p>
      {notes.map((n, i) => (
        <p className="field-note" key={i}>
          {n}
        </p>
      ))}
    </div>
  );
}
