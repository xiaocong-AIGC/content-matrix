import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Hourglass,
  PauseCircle,
  ShieldCheck,
  UserX,
  WifiOff,
  XCircle,
} from "lucide-react";

import { listAlerts, type AlertsResult, type OpsAlert } from "../lib/api";
import { ensureNotifyPermission, osNotify } from "../lib/notify";

const SEEN_KEY = "alert_last_seen";

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

function kindIcon(kind: string) {
  if (kind === "device_offline") return <WifiOff size={16} />;
  if (kind === "account_health") return <UserX size={16} />;
  if (kind === "task_failed" || kind === "task_failed_many")
    return <XCircle size={16} />;
  // 这两类说的是"什么都没发生"，和"某件事失败了"不是一回事：
  // 图标也要能一眼分开，否则铃铛里全是同一个红叉。
  if (kind === "task_stuck") return <PauseCircle size={16} />;
  if (kind === "queue_backlog") return <Hourglass size={16} />;
  return <AlertTriangle size={16} />;
}

export function AlertBell() {
  const [data, setData] = useState<AlertsResult | null>(null);
  const [open, setOpen] = useState(false);
  const [lastSeen, setLastSeen] = useState<string>(
    () => localStorage.getItem(SEEN_KEY) ?? "",
  );
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await listAlerts();
      setData(res);
      // OS 通知：出现比"已读线"更新的告警时推送一条（best-effort）。
      const seen = localStorage.getItem(SEEN_KEY) ?? "";
      const fresh = res.alerts.filter((a) => (a.at ?? "") > seen);
      if (fresh.length) {
        const top = fresh[0];
        osNotify(
          fresh.length > 1 ? `异常提醒 · ${fresh.length} 条` : "异常提醒",
          top.title,
        );
      }
    } catch {
      /* transient; best-effort */
    }
  }, []);

  useEffect(() => {
    ensureNotifyPermission();
    void load();
    const t = window.setInterval(() => void load(), 15000);
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

  const total = data?.total ?? 0;
  const critical = data?.counts.critical ?? 0;
  const alerts: OpsAlert[] = data?.alerts ?? [];
  const unread = alerts.filter((a) => (a.at ?? "") > lastSeen).length;

  function markAllRead() {
    const newest = alerts.reduce((m, a) => ((a.at ?? "") > m ? (a.at ?? "") : m), "");
    const seen = newest || new Date().toISOString();
    localStorage.setItem(SEEN_KEY, seen);
    setLastSeen(seen);
  }

  return (
    <div className="notif" ref={ref}>
      <button
        type="button"
        className={`notif-btn ${unread ? (critical ? "has-critical" : "has-warn") : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="异常提醒"
      >
        <AlertTriangle size={17} />
        {unread ? (
          <span className={`notif-badge ${critical ? "crit" : "warn"}`}>
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="notif-panel">
          <header className="notif-panel-head">
            <strong>异常提醒</strong>
            <div className="notif-head-right">
              <small>
                {total ? `${critical} 严重 · ${total - critical} 警告` : "全部正常"}
              </small>
              {unread ? (
                <button type="button" className="notif-clear" onClick={markAllRead}>
                  全部已读
                </button>
              ) : null}
            </div>
          </header>
          <div className="notif-list">
            {alerts.length ? (
              alerts.map((a) => (
                <div key={a.id} className={`notif-item alert-${a.severity}`}>
                  <span className="notif-icon">{kindIcon(a.kind)}</span>
                  <div className="notif-body">
                    <div className="notif-row1">
                      <strong>{a.title}</strong>
                      <time>{timeLabel(a.at)}</time>
                    </div>
                    <p className="notif-err">{a.detail}</p>
                  </div>
                </div>
              ))
            ) : (
              <div className="notif-empty">
                <ShieldCheck size={20} /> 设备、账号和发布都正常
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
