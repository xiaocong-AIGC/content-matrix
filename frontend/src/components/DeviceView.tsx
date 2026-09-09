import {
  BatteryCharging,
  Bot,
  Cpu,
  Radio,
  Smartphone,
  Trash2,
  Wifi,
} from "lucide-react";

import type { Device, PublishTask } from "../lib/types";
import { accountIdLabel, platformLabel } from "../lib/platform";

import { useConfirm } from "./ConfirmModal";
interface Props {
  devices: Device[];
  tasks: PublishTask[];
  onSelectDevice: (id: number) => void;
  onDelete: (device: Device) => void;
}

function heartbeat(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function DeviceView({ devices, tasks, onSelectDevice, onDelete }: Props) {
  const [confirm, confirmUI] = useConfirm();
  return (
    <div className="catalog-page page-enter">
      {confirmUI}
      <header className="catalog-hero">
        <div>
          <span>设备</span>
          <h2>每台手机，每个账号，一目了然。</h2>
          <p>
            一台手机可以绑定多个平台账号。点开设备卡片，就能看到它的任务、
            当前状态和发布时回传的截图。
          </p>
        </div>
        <div className="hero-counter">
          <strong>{String(devices.length).padStart(2, "0")}</strong>
          <span>REGISTERED<br />DEVICES</span>
        </div>
      </header>

      <section className="device-grid">
        {devices.length ? (
          devices.map((device, index) => {
            const task = tasks.find((item) => item.id === device.current_task_id);
            const online = device.status === "online" || device.status === "busy";
            return (
              <article
                className="device-card device-card-clickable"
                key={device.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelectDevice(device.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    onSelectDevice(device.id);
                  }
                }}
              >
                <div className="device-card-index">
                  <span>DEVICE</span>
                  <b>{String(index + 1).padStart(2, "0")}</b>
                </div>
                <div className="device-visual">
                  <div className="device-speaker" />
                  <Smartphone size={38} strokeWidth={1.1} />
                  <span className={online ? "online" : ""}>
                    <i />
                    {online ? "ONLINE" : "OFFLINE"}
                  </span>
                </div>
                <div className="device-content">
                  <div className="device-title">
                    <div>
                      <small>{device.device_code}</small>
                      <h3>{device.name}</h3>
                    </div>
                    <div className="device-title-actions">
                      <Radio size={18} />
                      <button
                        type="button"
                        className="device-delete"
                        title="删除设备（连同其所有账号）"
                        aria-label="删除设备"
                        onClick={async (event) => {
                          event.stopPropagation();
                          const accts = (device.accounts ?? [])
                            .map((a) => a.nickname || platformLabel(a.platform))
                            .join("、");
                          if (
                            await confirm({
                              title: `确定删除设备「${device.name}」？`,
                              detail:
                                `将一并移除其绑定账号${accts ? `：${accts}` : ""}。` +
                                `不影响手机本身，重新连接后会自动回到设备列表。`,
                              confirmText: "删除设备",
                              danger: true,
                            })
                          ) {
                            onDelete(device);
                          }
                        }}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                  <div className="device-accounts">
                    {(device.accounts ?? []).map((acc) => (
                      <div
                        key={acc.platform}
                        className="device-account-row"
                      >
                        <span className={`platform-badge plat-${acc.platform}`}>
                          {platformLabel(acc.platform)}
                        </span>
                        <strong>{acc.nickname || "未读取昵称"}</strong>
                        <small>
                          {acc.account_id
                            ? `${accountIdLabel(acc.platform)} ${acc.account_id}`
                            : "未读取账号"}
                          {" · "}
                          {acc.city || "未分组"}
                        </small>
                      </div>
                    ))}
                    {!(device.accounts ?? []).length ? (
                      <small className="device-account-empty">未读取到平台账号</small>
                    ) : null}
                  </div>
                  <div className="device-specs">
                    <span>
                      <Bot size={14} />
                      Agent {device.agent_version}
                    </span>
                    <span>
                      <Cpu size={14} />
                      Android {device.android_version ?? "--"}
                    </span>
                    <span>
                      <Wifi size={14} />
                      最近在线 {heartbeat(device.last_heartbeat_at)}
                    </span>
                    <span>
                      <BatteryCharging size={14} />
                      {device.status === "busy" ? "正在执行" : "等待任务"}
                    </span>
                  </div>
                  <div className="device-assignment">
                    <span>{task ? "正在执行" : "当前状态"}</span>
                    <strong>{task ? task.name : "空闲 · 点击查看任务"}</strong>
                  </div>
                </div>
              </article>
            );
          })
        ) : (
          <div className="catalog-empty">
            <Smartphone size={42} strokeWidth={1.2} />
            <h3>还没有连接手机</h3>
            <p>安装 Android Agent 后，设备会自动出现在这里。</p>
          </div>
        )}
      </section>
    </div>
  );
}
