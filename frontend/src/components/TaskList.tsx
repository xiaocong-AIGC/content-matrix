import { CalendarDays, ChevronRight, Smartphone } from "lucide-react";

import type { PublishTask } from "../lib/types";
import { StatusBadge } from "./StatusBadge";

export type TaskFilter = "all" | "active" | "waiting" | "failed";

interface Props {
  tasks: PublishTask[];
  selectedId?: number;
  filter: TaskFilter;
  onFilter: (filter: TaskFilter) => void;
  onSelect: (id: number) => void;
}

const filters: Array<{ id: TaskFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "active", label: "执行中" },
  { id: "waiting", label: "需介入" },
  { id: "failed", label: "异常" },
];

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function TaskList({
  tasks,
  selectedId,
  filter,
  onFilter,
  onSelect,
}: Props) {
  return (
    <div className="queue-board">
      <div className="queue-filter">
        {filters.map((item) => (
          <button
            key={item.id}
            className={filter === item.id ? "active" : ""}
            onClick={() => onFilter(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {!tasks.length ? (
        <div className="empty-state">
          <span className="empty-number">00</span>
          <strong>暂无发布任务</strong>
          <p>切换筛选条件，或点「新建发布」创建一条任务。</p>
        </div>
      ) : (
        <div className="task-list">
          {tasks.map((task, index) => (
            <button
              key={task.id}
              className={`task-row ${selectedId === task.id ? "selected" : ""}`}
              onClick={() => onSelect(task.id)}
            >
              <span className="task-order">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="task-main">
                <div className="task-kicker">
                  <span>任务 #{String(task.id).padStart(4, "0")}</span>
                  <StatusBadge status={task.status} />
                </div>
                <strong>{task.name}</strong>
                <p>{task.publish_title || task.body || "尚未填写发布文案"}</p>
                <div className="task-meta">
                  <span>
                    <Smartphone size={12} />
                    {task.device?.name ?? "等待设备"}
                  </span>
                  <span>
                    <CalendarDays size={12} />
                    {formatTime(task.created_at)}
                  </span>
                </div>
              </div>
              <div className="task-side">
                <span>{task.progress}%</span>
                <ChevronRight size={17} />
              </div>
              <div className="row-progress">
                <i style={{ width: `${task.progress}%` }} />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
