import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, CheckCircle2, XCircle } from "lucide-react";

import { listNotifications, type PublishNotification } from "../lib/api";
import { platformLabel } from "../lib/platform";
import { ensureNotifyPermission, osNotify } from "../lib/notify";

const SEEN_KEY = "notif_last_seen";

function timeLabel(iso: string | null): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

export function NotificationBell() {
  const [items, setItems] = useState<PublishNotification[]>([]);
  const [open, setOpen] = useState(false);
  const [lastSeen, setLastSeen] = useState<string>(
    () => localStorage.getItem(SEEN_KEY) ?? "",
  );
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const list = await listNotifications();
      setItems(list);
      // OS 通知：有比"已读线"更新的发布结果时推送一条。
      const seen = localStorage.getItem(SEEN_KEY) ?? "";
      const fresh = list.filter((n) => (n.finished_at ?? "") > seen);
      if (fresh.length) {
        const top = fresh[0];
        osNotify(
          `${top.ok ? "发布成功" : "发布失败"}：${top.account}`,
          `${platformLabel(top.platform)} · ${top.title}`,
        );
      }
    } catch {
      /* transient; the bell is best-effort */
    }
  }, []);

  useEffect(() => {
    ensureNotifyPermission();
    void load();
    const t = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const unread = items.filter((n) => (n.finished_at ?? "") > lastSeen).length;

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && items.length) {
      const newest = items[0].finished_at ?? new Date().toISOString();
      localStorage.setItem(SEEN_KEY, newest);
      setLastSeen(newest);
    }
  }

  return (
    <div className="notif" ref={ref}>
      <button
        type="button"
        className={`notif-btn ${unread ? "has-unread" : ""}`}
        onClick={toggle}
        title="发布通知"
      >
        <Bell size={17} />
        {unread ? <span className="notif-badge">{unread > 99 ? "99+" : unread}</span> : null}
      </button>
      {open ? (
        <div className="notif-panel">
          <header className="notif-panel-head">
            <strong>发布通知</strong>
            <small>最近 {items.length} 条结果</small>
          </header>
          <div className="notif-list">
            {items.length ? (
              items.map((n) => (
                <div key={n.id} className={`notif-item ${n.ok ? "ok" : "fail"}`}>
                  <span className="notif-icon">
                    {n.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
                  </span>
                  <div className="notif-body">
                    <div className="notif-row1">
                      <span className={`platform-badge plat-${n.platform}`}>
                        {n.kind === "group_message" ? "群发" : platformLabel(n.platform)}
                      </span>
                      <strong>{n.account}</strong>
                      {n.is_fanout ? <em className="notif-fanout">跨平台</em> : null}
                      <time>{timeLabel(n.finished_at)}</time>
                    </div>
                    <p className="notif-title">{n.title}</p>
                    {!n.ok && n.error ? (
                      <p className="notif-err">{n.error}</p>
                    ) : null}
                  </div>
                </div>
              ))
            ) : (
              <div className="notif-empty">暂无发布结果</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
