/**
 * A guaranteed 24-hour date+time picker. The native <input type="datetime-local">
 * renders 上午/下午 under a 12-hour OS locale (which we can't override), so we use
 * a date input + 两个统一的 Select 选时/分。值的格式和 datetime-local 一样：
 * "YYYY-MM-DDTHH:MM"。
 *
 * 时/分本来是原生 <select>，跟着全站一起收口了 —— 留两个原生下拉在这里，
 * 就是"大部分统一了、除了这一个"，而用户问的是「所有的这种下拉都统一了对吗」。
 */
import { Select } from "./Select";

const pad = (n: number) => String(n).padStart(2, "0");
const HOURS = Array.from({ length: 24 }, (_, i) => pad(i));
const MINUTES = Array.from({ length: 12 }, (_, i) => pad(i * 5)); // 00,05,…,55

interface Props {
  value: string; // "YYYY-MM-DDTHH:MM"
  min?: string; // lower bound (only the date part is used)
  onChange: (value: string) => void;
}

export function DateTime24({ value, min, onChange }: Props) {
  const [datePart = "", timePart = "00:00"] = (value || "").split("T");
  const [hh = "00", rawMm = "00"] = timePart.split(":");
  // Snap an arbitrary minute to the nearest 5-min option so it always matches.
  const mm = MINUTES.includes(rawMm)
    ? rawMm
    : pad(Math.round(Number(rawMm || 0) / 5) * 5 === 60 ? 55 : Math.round(Number(rawMm || 0) / 5) * 5);
  const minDate = min ? min.split("T")[0] : undefined;

  return (
    <div className="dt24">
      <input
        type="date"
        value={datePart}
        min={minDate}
        onChange={(e) => onChange(`${e.target.value}T${hh}:${mm}`)}
      />
      <div className="dt24-time">
        <Select
          value={hh}
          options={HOURS.map((h) => ({ value: h, label: h }))}
          onChange={(v) => onChange(`${datePart}T${v}:${mm}`)}
        />
        <span>:</span>
        <Select
          value={mm}
          options={MINUTES.map((m) => ({ value: m, label: m }))}
          onChange={(v) => onChange(`${datePart}T${hh}:${v}`)}
        />
        <span className="dt24-24h">24h</span>
      </div>
    </div>
  );
}
