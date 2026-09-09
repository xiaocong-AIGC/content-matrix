import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, CalendarDays, Radio, Smartphone } from "lucide-react";

import type { TaskActionKind } from "../lib/api";
import type { Device, PublishTask, TaskStatus } from "../lib/types";
import { platformLabel } from "../lib/platform";
import { FilterDropdown } from "./FilterDropdown";
import { StatusBadge } from "./StatusBadge";
import { TaskDetail } from "./TaskDetail";

const groups: Array<{ label: string; statuses: TaskStatus[] }> = [
  { label: "执行中", statuses: ["running", "leased"] },
  { label: "需人工介入", statuses: ["waiting_confirmation"] },
  { label: "待执行", statuses: ["queued"] },
  { label: "已完成", statuses: ["succeeded"] },
  { label: "失败 / 取消", statuses: ["failed", "cancelled"] },
];

interface Props {
  device: Device;
  tasks: PublishTask[];
  initialPlatform?: string;
  selectedTask?: PublishTask;
  onSelectTask: (id: number) => void;
  onAction: (id: number, action: TaskActionKind) => Promise<void>;
  onBack: () => void;
  backLabel?: string;
}

// The time that best represents a task on the timeline.
function taskMoment(task: PublishTask): string {
  return task.finished_at ?? task.scheduled_at ?? task.created_at;
}

function localDateStr(value: string) {
  const d = new Date(value);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function todayStr(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function timeLabel(task: PublishTask) {
  const fmt = (v: string) =>
    new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(v));
  if (task.finished_at) return `发布 ${fmt(task.finished_at)}`;
  if (task.scheduled_at) return `定时 ${fmt(task.scheduled_at)}`;
  return `创建 ${fmt(task.created_at)}`;
}

export function DeviceTasksView({
  device,
  tasks,
  initialPlatform,
  selectedTask,
  onSelectTask,
  onAction,
  onBack,
  backLabel = "返回设备列表",
}: Props) {
  const [dateFilter, setDateFilter] = useState(""); // "" = 全部
  const [kind, setKind] = useState<"post" | "broadcast">("post");
  const platforms = (device.accounts ?? []).map((a) => a.platform);
  const [platform, setPlatform] = useState(
    initialPlatform ?? platforms[0] ?? "douyin",
  );
  // Follow the platform chosen on the account card when navigating in.
  useEffect(() => {
    if (initialPlatform) setPlatform(initialPlatform);
  }, [initialPlatform]);

  const deviceTasks = useMemo(
    () =>
      tasks.filter((task) => {
        const mine =
          task.target_device_id === device.id || task.device?.id === device.id;
        if (!mine) return false;
        const isBroadcast = task.publish_type === "group_message";
        if (kind === "broadcast") return isBroadcast; // 群发 = douyin only
        // 内容发布: also filter by platform account.
        return !isBroadcast && (task.platform || "douyin") === platform;
      }),
    [tasks, device.id, kind, platform],
  );

  const filtered = useMemo(
    () =>
      dateFilter
        ? deviceTasks.filter((task) => localDateStr(taskMoment(task)) === dateFilter)
        : deviceTasks,
    [deviceTasks, dateFilter],
  );

  // Only show a task in the detail panel if it belongs to the CURRENT platform
  // filter — otherwise switching 抖音/小红书 would leave a cross-platform task open.
  const detailTask = useMemo(
    () =>
      selectedTask && deviceTasks.some((t) => t.id === selectedTask.id)
        ? selectedTask
        : undefined,
    [selectedTask, deviceTasks],
  );

  const online = device.status === "online" || device.status === "busy";
  const publishedToday = deviceTasks.filter(
    (task) =>
      task.status === "succeeded" &&
      task.finished_at &&
      localDateStr(task.finished_at) === todayStr(),
  ).length;
  const dayPublished = filtered.filter((t) => t.status === "succeeded").length;

  const filters = [
    { key: "", label: "全部" },
    { key: todayStr(), label: "今天" },
    { key: todayStr(-1), label: "昨天" },
  ];

  return (
    <div className="device-tasks page-enter">
      <div className="device-tasks-sticky">
      <header className="device-tasks-head">
        <button className="back-button" onClick={onBack}>
          <ArrowLeft size={15} />
          {backLabel}
        </button>
        <div className="device-tasks-id">
          <span>账号矩阵</span>
          <h2>
            <Radio size={18} />
            {device.name}
          </h2>
          <small>
            <Smartphone size={11} /> {device.device_code} ·{" "}
            {online ? (device.status === "busy" ? "执行中" : "在线") : "离线"}
          </small>
          <div className="dta-accounts">
            {(device.accounts ?? []).length ? (
              (device.accounts ?? []).map((a) => (
                <span key={a.platform} className="dta-account">
                  <span className={`platform-badge plat-${a.platform}`}>
                    {platformLabel(a.platform)}
                  </span>
                  <strong>{a.nickname || "未读取昵称"}</strong>
                </span>
              ))
            ) : (
              <span className="dta-account-empty">未读取到平台账号</span>
            )}
          </div>
        </div>
      </header>

      {!online &&
      deviceTasks.some((task) =>
        ["queued", "leased", "running", "waiting_confirmation"].includes(
          task.status,
        ),
      ) ? (
        <div className="offline-banner">
          该设备当前离线，待执行的任务会在设备上线后自动开始。
        </div>
      ) : null}

      <div className="kind-tabs">
        <button
          className={kind === "post" ? "active" : ""}
          onClick={() => setKind("post")}
        >
          内容发布
        </button>
        <button
          className={kind === "broadcast" ? "active" : ""}
          onClick={() => setKind("broadcast")}
        >
          群消息
        </button>
      </div>

      <div className="device-date-bar">
        {kind === "post" && platforms.length > 1 ? (
          <FilterDropdown
            label="平台"
            value={platform}
            onChange={setPlatform}
            options={platforms.map((p) => ({
              value: p,
              label: platformLabel(p),
            }))}
          />
        ) : null}
        <FilterDropdown
          label="时间"
          icon={<CalendarDays size={13} />}
          value={dateFilter || "all"}
          onChange={(v) => setDateFilter(v === "all" ? "" : v)}
          options={filters.map((f) => ({
            value: f.key || "all",
            label: f.label,
          }))}
        />
        <input
          type="date"
          value={dateFilter}
          onChange={(event) => setDateFilter(event.target.value)}
        />
        <span className="date-summary">
          {dateFilter ? `${dateFilter} · ` : "全部 · "}共 {filtered.length} 条 · 成功{" "}
          {dayPublished} 条
        </span>
      </div>
      </div>

      <div className="workspace">
        <section className="queue-column">
          <div className="queue-board">
            {filtered.length ? (
              groups.map((group) => {
                const items = filtered.filter((task) =>
                  group.statuses.includes(task.status),
                );
                if (!items.length) return null;
                return (
                  <div key={group.label} className="status-group">
                    <header className="status-group-head">
                      <span>{group.label}</span>
                      <small>{items.length}</small>
                    </header>
                    {items.map((task) => (
                      <button
                        key={task.id}
                        className={`task-row device-task-row ${
                          selectedTask?.id === task.id ? "selected" : ""
                        }`}
                        onClick={() => onSelectTask(task.id)}
                      >
                        <div className="task-main">
                          <div className="task-kicker">
                            <span>任务 #{String(task.id).padStart(4, "0")}</span>
                            <StatusBadge status={task.status} />
                          </div>
                          <strong>{task.name}</strong>
                          <p>
                            {task.publish_title || task.body || "尚未填写发布文案"}
                          </p>
                          <div className="task-meta">
                            <span>
                              <CalendarDays size={12} />
                              {timeLabel(task)}
                            </span>
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                );
              })
            ) : (
              <div className="empty-state">
                <span className="empty-number">00</span>
                <strong>{dateFilter ? "该日暂无任务" : "该设备暂无任务"}</strong>
                <p>切换日期，或在内容库安排该账号的发布。</p>
              </div>
            )}
          </div>
        </section>

        <section className="detail-column">
          <TaskDetail task={detailTask} onAction={onAction} />
        </section>
      </div>
    </div>
  );
}
