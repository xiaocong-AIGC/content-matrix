import { CalendarDays, FileCheck2, Smartphone, UserRound } from "lucide-react";

import type { PublishTask } from "../lib/types";
import { platformLabel } from "../lib/platform";
import { Pager, usePaged } from "./Pager";
import { StatusBadge } from "./StatusBadge";

export function HistoryView({ tasks }: { tasks: PublishTask[] }) {
  const records = tasks
    .filter((task) => ["succeeded", "failed", "cancelled"].includes(task.status))
    .sort((a, b) => b.id - a.id);
  const paged = usePaged(records, 10);

  return (
    <div className="catalog-page page-enter">
      <header className="catalog-hero history-hero">
        <div>
          <span>记录</span>
          <h2>每一次发布，都有据可查。</h2>
          <p>完成、失败和已取消的任务都归档在这里，可以看到账号、时间和结果。</p>
        </div>
        <FileCheck2 size={72} strokeWidth={0.8} />
      </header>

      <section className="history-table">
        <header>
          <span>任务 / 内容</span>
          <span>发布设备 / 账号</span>
          <span>完成时间</span>
          <span>结果</span>
        </header>
        {records.length ? (
          paged.slice.map((task) => (
            <article key={task.id}>
              <div>
                <small>#{String(task.id).padStart(4, "0")}</small>
                <strong>
                  <span
                    className={`platform-badge plat-${
                      task.publish_type === "group_message"
                        ? "douyin"
                        : task.platform || "douyin"
                    }`}
                  >
                    {task.publish_type === "group_message"
                      ? "群发"
                      : platformLabel(task.platform)}
                  </span>{" "}
                  {task.name}
                </strong>
                <p>{task.publish_title || task.body}</p>
              </div>
              <span className="history-deviceacct">
                <span className="history-device">
                  <Smartphone size={14} />
                  <b className="meta-nick">{task.device?.name ?? "未记录设备"}</b>
                </span>
                <span className="history-account">
                  <UserRound size={13} />
                  {task.publish_type === "group_message"
                    ? "群发"
                    : task.account_nickname ??
                      task.device?.douyin_nickname ??
                      "未绑定账号"}
                </span>
              </span>
              <span>
                <CalendarDays size={14} />
                <b className="meta-time">
                  {new Intl.DateTimeFormat("zh-CN", {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                    hour12: false,
                  }).format(new Date(task.updated_at))}
                </b>
              </span>
              <StatusBadge status={task.status} />
            </article>
          ))
        ) : (
          <div className="catalog-empty compact">
            <FileCheck2 size={36} strokeWidth={1.1} />
            <h3>暂无归档记录</h3>
            <p>完成、失败或取消的任务会出现在这里。</p>
          </div>
        )}
      </section>
      <Pager
        page={paged.page}
        pageCount={paged.pageCount}
        total={paged.total}
        onChange={paged.setPage}
      />
    </div>
  );
}
