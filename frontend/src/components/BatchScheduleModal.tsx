import { useMemo, useState } from "react";
import { CalendarClock, Check, Layers, Plus, Trash2, X } from "lucide-react";

import { scheduleBatch } from "../lib/api";
import type { BatchScheduleResult } from "../lib/api";
import type { Device, DeviceAccount } from "../lib/types";
import { DateTime24 } from "./DateTime24";

interface Props {
  devices: Device[];
  contentPending: number;
  onClose: () => void;
  onDone: (message: string) => void;
}

const PLATFORM_LABEL: Record<string, string> = { douyin: "抖音", xhs: "小红书" };

function pad(n: number) {
  return String(n).padStart(2, "0");
}
function slotAt(dayOffset: number, hour: number, minute = 0) {
  const d = new Date();
  d.setDate(d.getDate() + dayOffset);
  d.setHours(hour, minute, 0, 0);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
function dayLabel(value: string) {
  if (!value) return "未设置";
  const d = new Date(value);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${WEEK[d.getDay()]}`;
}

interface Row {
  device: Device;
  account: DeviceAccount;
}
function accountRows(devices: Device[], platform: string): Row[] {
  const rows: Row[] = [];
  for (const device of devices) {
    const accts = device.accounts?.length
      ? device.accounts
      : [
          {
            id: -device.id,
            device_id: device.id,
            platform: "douyin",
            nickname: device.douyin_nickname ?? null,
            account_id: device.douyin_id ?? null,
            city: device.city || "未分组",
            daily_quota: device.daily_quota ?? 2,
            health: device.health ?? "normal",
          } as DeviceAccount,
        ];
    for (const account of accts) {
      if (account.platform === platform) rows.push({ device, account });
    }
  }
  return rows;
}
function isOnline(device: Device) {
  return device.status === "online" || device.status === "busy";
}
// An account we'd actually publish to: online, a11y ready, logged in, healthy.
function usable(row: Row) {
  return (
    isOnline(row.device) &&
    row.device.accessibility_ok !== false &&
    row.account.logged_in !== false &&
    (row.account.health ?? "normal") === "normal"
  );
}

export function BatchScheduleModal({
  devices,
  contentPending,
  onClose,
  onDone,
}: Props) {
  const [platform, setPlatform] = useState("douyin");
  const [slots, setSlots] = useState<string[]>([slotAt(0, 19, 0), slotAt(1, 9, 0)]);
  const [selected, setSelected] = useState<Set<number> | null>(null); // null = "all usable"
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BatchScheduleResult | null>(null);

  const rows = useMemo(() => accountRows(devices, platform), [devices, platform]);
  const usableRows = useMemo(() => rows.filter(usable), [rows]);
  // Default selection = every usable account until the operator toggles one.
  const isSelected = (id: number) =>
    selected ? selected.has(id) : usableRows.some((r) => r.device.id === id);
  const selectedIds = usableRows
    .filter((r) => isSelected(r.device.id))
    .map((r) => r.device.id);

  function toggle(id: number) {
    const base = new Set(
      selected ?? usableRows.map((r) => r.device.id),
    );
    if (base.has(id)) base.delete(id);
    else base.add(id);
    setSelected(base);
  }
  const validSlots = slots.filter(Boolean);
  const totalPosts = selectedIds.length * validSlots.length;

  function setSlot(i: number, value: string) {
    const next = [...slots];
    next[i] = value;
    setSlots(next);
  }
  function addSlot() {
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
  }
  function removeSlot(i: number) {
    setSlots(slots.filter((_, idx) => idx !== i));
  }

  async function submit() {
    if (selectedIds.length === 0) {
      setError("请至少选择一个账号");
      return;
    }
    if (validSlots.length === 0) {
      setError("请至少添加一个发布时段");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const r = await scheduleBatch(
        selectedIds,
        validSlots.map((t) => new Date(t).toISOString()),
        platform,
      );
      setResult(r);
      const parts = [`已为 ${r.accounts} 个账号安排 ${r.total_scheduled} 条`];
      if (r.pool_exhausted) parts.push("内容库待发不足，部分未排满");
      onDone(parts.join("，"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "批量排期失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="schedule-modal batch-modal"
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="schedule-head">
          <div>
            <span>
              <Layers size={13} /> 跨账号批量排期
            </span>
            <strong>一次给多个账号排期</strong>
            <small>
              同样的时段发到每个选中账号，内容自动分配、不重复。内容库待发 {contentPending} 条。
            </small>
          </div>
          <button className="ghost-icon" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="schedule-body">
          {result ? (
            <div className="batch-result">
              <p className="schedule-note">
                已为 {result.accounts} 个账号安排 {result.total_scheduled} 条。
                {result.pool_exhausted ? "（内容库待发不足，部分账号没排满，去内容库补内容）" : ""}
              </p>
              <ul className="batch-result-list">
                {result.results.map((r) => (
                  <li key={r.device_id}>
                    <span>{r.name}</span>
                    <em>
                      {r.error
                        ? r.error
                        : `排 ${r.scheduled} 条${
                            r.skipped_quota ? `，${r.skipped_quota} 超配额` : ""
                          }${r.pool_exhausted ? "，内容不足" : ""}`}
                    </em>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <>
              <div className="batch-platform">
                {(["douyin", "xhs"] as const).map((p) => (
                  <button
                    key={p}
                    type="button"
                    className={`chip ${platform === p ? "on" : ""}`}
                    onClick={() => {
                      setPlatform(p);
                      setSelected(null);
                    }}
                  >
                    {PLATFORM_LABEL[p]}
                  </button>
                ))}
              </div>

              <p className="schedule-note">
                选择账号（默认全选可用的），再设定发布时段。
              </p>

              <div className="batch-accounts">
                {usableRows.length === 0 ? (
                  <p className="schedule-warn">
                    没有可用的{PLATFORM_LABEL[platform]}账号，需要设备在线、无障碍就绪、账号已登录。
                  </p>
                ) : (
                  usableRows.map((r) => (
                    <label key={r.device.id} className="batch-account">
                      <input
                        type="checkbox"
                        checked={isSelected(r.device.id)}
                        onChange={() => toggle(r.device.id)}
                      />
                      <span className="batch-account-name">
                        {r.account.nickname || r.device.name}
                      </span>
                      <small>{r.account.city}</small>
                    </label>
                  ))
                )}
              </div>

              <div className="batch-slots">
                {slots.map((value, i) => (
                  <div className="schedule-field schedule-slot" key={i}>
                    <span>{dayLabel(value)}</span>
                    <DateTime24
                      value={value}
                      min={slotAt(
                        0,
                        new Date().getHours(),
                        new Date().getMinutes(),
                      )}
                      onChange={(v) => setSlot(i, v)}
                    />
                    <button
                      className="ghost-icon"
                      aria-label="删除时段"
                      onClick={() => removeSlot(i)}
                      disabled={slots.length <= 1}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
                <button className="button secondary add-slot" onClick={addSlot}>
                  <Plus size={15} /> 添加发布时段
                </button>
              </div>

              {totalPosts > contentPending ? (
                <p className="schedule-warn">
                  共 {selectedIds.length} 个账号 × {validSlots.length} 个时段 ={" "}
                  {totalPosts} 条，内容库待发仅 {contentPending} 条，会少排。先补内容。
                </p>
              ) : null}
              {error ? <p className="form-error">{error}</p> : null}
            </>
          )}
        </div>

        <footer className="schedule-foot">
          <button className="button secondary" onClick={onClose}>
            {result ? "关闭" : "取消"}
          </button>
          {!result ? (
            <button
              className="button primary"
              disabled={submitting || selectedIds.length === 0}
              onClick={() => void submit()}
            >
              {submitting ? (
                "正在排期…"
              ) : (
                <>
                  <CalendarClock size={15} /> 给 {selectedIds.length} 个账号排期
                </>
              )}
            </button>
          ) : null}
        </footer>
      </section>
    </div>
  );
}
