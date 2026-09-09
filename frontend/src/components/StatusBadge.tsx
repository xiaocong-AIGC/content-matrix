import type { TaskStatus } from "../lib/types";

const labels: Record<TaskStatus, string> = {
  queued: "排队中",
  leased: "已分配设备",
  running: "执行中",
  waiting_confirmation: "需人工介入",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={`status-badge status-${status}`}>
      <i />
      {labels[status]}
    </span>
  );
}
