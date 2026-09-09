import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Check, Plus, Trash2, X } from "lucide-react";

import { listContent, schedulePosts } from "../lib/api";
import type { ContentItem, Device, DeviceAccount } from "../lib/types";
import { DateTime24 } from "./DateTime24";
import { Select } from "./Select";

// Cities treated as "everyone" — a generic piece is pickable for any account.
const GENERIC_CITIES = ["通用", "全国"];

interface Props {
  device: Device;
  account: DeviceAccount;
  contentPending: number;
  onClose: () => void;
  onDone: (message: string) => void;
}

const PLATFORM_LABEL: Record<string, string> = { douyin: "抖音", xhs: "小红书" };

function pad(n: number) {
  return String(n).padStart(2, "0");
}

// datetime-local value (local time) at a day offset and a given hour.
function slotAt(dayOffset: number, hour: number, minute = 0) {
  const d = new Date();
  d.setDate(d.getDate() + dayOffset);
  d.setHours(hour, minute, 0, 0);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

// Group label like "06-18 周三" for a datetime-local string.
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
function dayLabel(value: string) {
  if (!value) return "未设置";
  const d = new Date(value);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${WEEK[d.getDay()]}`;
}

export function SchedulePublishModal({
  device,
  account,
  contentPending,
  onClose,
  onDone,
}: Props) {
  const quota = account.daily_quota ?? 2;
  // Default: two slots — later today and tomorrow morning — as a starting point.
  const [slots, setSlots] = useState<string[]>([
    slotAt(0, 19, 0),
    slotAt(1, 9, 0),
  ]);
  // Per-slot pinned content id (null = 随机). Kept parallel to `slots`.
  const [slotContent, setSlotContent] = useState<(number | null)[]>([
    null,
    null,
  ]);
  const [pickable, setPickable] = useState<ContentItem[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Load the pending pool this account could actually publish: platform-compatible
  // (own platform or `both`) and either its own city or a generic piece.
  useEffect(() => {
    let alive = true;
    listContent()
      .then((items) => {
        if (!alive) return;
        setPickable(
          items.filter(
            (c) =>
              c.status === "pending" &&
              (c.platform === account.platform || c.platform === "both") &&
              (c.city === account.city || GENERIC_CITIES.includes(c.city)),
          ),
        );
      })
      .catch(() => {
        /* picker is optional — silent on failure, 排期 still works as 随机 */
      });
    return () => {
      alive = false;
    };
  }, [account.platform, account.city]);

  const valid = slots.filter(Boolean);

  // Per-day counts, to warn when a single day exceeds the account quota.
  const perDay = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of valid) {
      const key = dayLabel(s);
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return map;
  }, [valid]);
  const overQuotaDays = [...perDay.entries()].filter(([, n]) => n > quota);

  function setSlot(i: number, value: string) {
    const next = [...slots];
    next[i] = value;
    setSlots(next);
  }
  function setSlotPick(i: number, contentId: number | null) {
    const next = [...slotContent];
    next[i] = contentId;
    setSlotContent(next);
  }
  function addSlot() {
    // New slot defaults to the day after the last one, same hour.
    const last = slots[slots.length - 1];
    if (last) {
      const d = new Date(last);
      d.setDate(d.getDate() + 1);
      setSlots([
        ...slots,
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
          d.getHours(),
        )}:${pad(d.getMinutes())}`,
      ]);
    } else {
      setSlots([...slots, slotAt(0, 12, 0)]);
    }
    setSlotContent([...slotContent, null]);
  }
  function removeSlot(i: number) {
    setSlots(slots.filter((_, idx) => idx !== i));
    setSlotContent(slotContent.filter((_, idx) => idx !== i));
  }

  // A content pinned to another slot can't be picked twice in this dialog.
  const pinnedElsewhere = (i: number) =>
    new Set(slotContent.filter((id, idx) => id != null && idx !== i));
  function pickLabel(c: ContentItem) {
    const name = c.title || c.cover_title || c.body.slice(0, 16) || "无标题";
    const cityTag = GENERIC_CITIES.includes(c.city) ? "" : `（${c.city}）`;
    return `${name}${cityTag}`;
  }

  async function submit() {
    if (valid.length === 0) {
      setError("请至少添加一个发布时段");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      // Align pins to the kept (non-empty) slots, same order as `valid`.
      const pins = slots
        .map((s, i) => [s, slotContent[i] ?? null] as const)
        .filter(([s]) => Boolean(s))
        .map(([, id]) => id);
      const anyPinned = pins.some((id) => id != null);
      const result = await schedulePosts(
        device.id,
        valid.map((t) => new Date(t).toISOString()),
        account.platform,
        anyPinned ? pins : undefined,
      );
      const parts = [`已安排 ${result.scheduled} 条`];
      if (result.skipped_quota > 0)
        parts.push(`${result.skipped_quota} 条超出当天上限没排上`);
      if (result.pool_exhausted) parts.push("内容库待发不足");
      onDone(parts.join("，"));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "排期失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="schedule-modal"
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="schedule-head">
          <div>
            <span>
              <CalendarClock size={13} /> 定时排期发布 ·{" "}
              {PLATFORM_LABEL[account.platform] ?? account.platform}
            </span>
            <strong>{account.nickname || device.name}</strong>
            <small>
              {PLATFORM_LABEL[account.platform] ?? ""}号{" "}
              {account.account_id || "未读取"} · 每天最多 {quota} 条 · 可选内容{" "}
              {pickable.length} 条
            </small>
          </div>
          <button className="ghost-icon" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="schedule-body">
          <ul className="schedule-note">
            <li>不指定内容的话，到点从内容库随机取一条。</li>
            <li>指定了就锁住这条，别的账号不会再取到，避免重复发布。</li>
            <li>同一天最多 {quota} 条，超出的不排。</li>
          </ul>

          {slots.map((value, i) => {
            const taken = pinnedElsewhere(i);
            return (
              <div className="schedule-field schedule-slot" key={i}>
                <span>{dayLabel(value)}</span>
                <DateTime24
                  value={value}
                  min={slotAt(0, new Date().getHours(), new Date().getMinutes())}
                  onChange={(v) => setSlot(i, v)}
                />
                <button
                  className="ghost-icon"
                  aria-label="删除这个时段"
                  title="删除这个时段"
                  onClick={() => removeSlot(i)}
                  disabled={slots.length <= 1}
                >
                  <Trash2 size={15} />
                </button>
                <div className="schedule-pick">
                  <Select
                    variant="field"
                    value={slotContent[i] ? String(slotContent[i]) : ""}
                    placeholder="随机取一条（默认）"
                    options={[
                      { value: "", label: "随机取一条（默认）" },
                      ...pickable.map((c) => ({
                        value: String(c.id),
                        label: c.cover_title || c.title || c.body.slice(0, 20),
                      })),
                    ]}
                    onChange={(v) => setSlotPick(i, v ? Number(v) : null)}
                  />
                </div>
                {(() => {
                  const picked = pickable.find((c) => c.id === slotContent[i]);
                  if (!picked) return null;
                  return (
                    <div className="schedule-preview">
                      <strong>
                        {picked.cover_title || picked.title || "无标题"}
                      </strong>
                      <p>{picked.body}</p>
                      {picked.topics.length > 0 ? (
                        <span className="schedule-preview-tags">
                          {picked.topics.map((t) => `#${t}`).join(" ")}
                        </span>
                      ) : null}
                    </div>
                  );
                })()}
              </div>
            );
          })}

          <button className="button secondary add-slot" onClick={addSlot}>
            <Plus size={15} /> 添加发布时段
          </button>

          {overQuotaDays.length > 0 ? (
            <p className="schedule-warn">
              {overQuotaDays.map(([d, n]) => `${d} 安排了 ${n} 条`).join("、")}
              ，超过每天 {quota} 条上限，超出的不会排上。
            </p>
          ) : null}
          {valid.length > contentPending ? (
            <p className="schedule-warn">
              共 {valid.length} 个时段，内容库待发仅 {contentPending} 条，会少排几条。先去内容库补内容。
            </p>
          ) : null}
          {error ? <p className="form-error">{error}</p> : null}
        </div>

        <footer className="schedule-foot">
          <button className="button secondary" onClick={onClose}>
            取消
          </button>
          <button
            className="button primary"
            disabled={submitting || valid.length === 0}
            onClick={() => void submit()}
          >
            <Check size={15} />
            {submitting ? "正在安排…" : `确认排期 ${valid.length} 条`}
          </button>
        </footer>
      </section>
    </div>
  );
}
