import { useMemo, useState, type FormEvent } from "react";
import {
  Check,
  ChevronRight,
  FileText,
  Hash,
  ShieldCheck,
  Smartphone,
  Type,
  X,
} from "lucide-react";

import type { Device, TaskCreate } from "../lib/types";
import { platformLabel } from "../lib/platform";
import { DateTime24 } from "./DateTime24";
import { Select } from "./Select";

interface Props {
  devices: Device[];
  onClose: () => void;
  onSubmit: (payload: TaskCreate) => Promise<void>;
}

// datetime-local lower bound: now (can't schedule in the past).
function nowLocal() {
  const p = (n: number) => String(n).padStart(2, "0");
  const d = new Date();
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(
    d.getHours(),
  )}:${p(d.getMinutes())}`;
}

const initialForm: TaskCreate = {
  name: "",
  platform: "douyin",
  cover_title: "",
  publish_title: "",
  body: "",
  topics: [],
  publish_mode: "auto_publish",
  priority: 50,
};

export function CreateTaskModal({ devices, onClose, onSubmit }: Props) {
  const claimableDevices = useMemo(
    () =>
      devices.filter(
        (device) => device.status === "online" || device.status === "busy",
      ),
    [devices],
  );

  const [form, setForm] = useState<TaskCreate>(() => ({
    ...initialForm,
    target_device_id: claimableDevices[0]?.id,
  }));
  const [topics, setTopics] = useState("");
  const [schedule, setSchedule] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const topicPreview = useMemo(
    () =>
      topics
        .split(/[,，\s#]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    [topics],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.target_device_id) {
      setError("请选择发布设备");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await onSubmit({
        ...form,
        topics: topicPreview,
        scheduled_at: schedule ? new Date(schedule).toISOString() : null,
      });
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建任务失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-task-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <aside className="modal-rail">
          <span className="modal-edition">新建任务</span>
          <div>
            <small>NEW TASK</small>
            <h2 id="create-task-title">新建一条发布任务</h2>
            <p>填写内容并选择发布设备，到点后自动发布。</p>
          </div>
          <ol>
            <li className="active">
              <span>01</span>
              填写内容
            </li>
            <li>
              <span>02</span>
              选择设备
            </li>
            <li>
              <span>03</span>
              自动发布
            </li>
          </ol>
          <div className="modal-rail-note">
            <ShieldCheck size={18} />
            发布完成后会自动回传结果截图，可在执行记录里查看。
          </div>
        </aside>

        <div className="modal-workspace">
          <header className="modal-header">
            <div>
              <span>抖音发布</span>
              <strong>发布内容</strong>
            </div>
            <button className="ghost-icon" onClick={onClose} aria-label="关闭">
              <X size={19} />
            </button>
          </header>

          <form onSubmit={submit}>
            <div className="form-section">
              <div className="form-section-title">
                <FileText size={17} />
                <span>
                  <b>01</b>
                  任务名称
                </span>
              </div>
              <label className="field">
                <span>任务名称</span>
                <input
                  required
                  autoFocus
                  value={form.name}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                  placeholder="例如：周五城市漫游图文"
                />
              </label>
            </div>

            <div className="form-section">
              <div className="form-section-title">
                <Type size={17} />
                <span>
                  <b>02</b>
                  标题与正文
                </span>
              </div>
              <div className="form-grid">
                <label className="field">
                  <span>封面标题</span>
                  <input
                    value={form.cover_title}
                    onChange={(event) =>
                      setForm({ ...form, cover_title: event.target.value })
                    }
                    placeholder="封面上的短标题"
                  />
                </label>
                <label className="field">
                  <span>发布标题</span>
                  <input
                    value={form.publish_title}
                    onChange={(event) =>
                      setForm({ ...form, publish_title: event.target.value })
                    }
                    placeholder="发布页上的标题"
                  />
                </label>
                <label className="field span-2">
                  <span>正文内容</span>
                  <textarea
                    rows={6}
                    value={form.body}
                    onChange={(event) =>
                      setForm({ ...form, body: event.target.value })
                    }
                    placeholder="要发出去的正文内容"
                  />
                  <small>{form.body.length} / 5000</small>
                </label>
              </div>
            </div>

            <div className="form-section">
              <div className="form-section-title">
                <Hash size={17} />
                <span>
                  <b>03</b>
                  发布设备与调度
                </span>
              </div>
              <label className="field">
                <span>话题标签</span>
                <input
                  value={topics}
                  onChange={(event) => setTopics(event.target.value)}
                  placeholder="城市漫游, 周末去哪儿, 生活记录"
                />
                <div className="topic-preview">
                  {topicPreview.map((topic) => (
                    <span key={topic}>#{topic}</span>
                  ))}
                </div>
              </label>
              <div className="form-grid compact-grid">
                <label className="field">
                  <span>
                    <Smartphone size={11} /> 发布设备 · 必选
                  </span>
                  <Select
                    variant="field"
                    value={form.target_device_id ? String(form.target_device_id) : ""}
                    placeholder="选一台设备"
                    options={claimableDevices.map((d) => ({
                      value: String(d.id),
                      label: d.douyin_nickname || d.name,
                    }))}
                    onChange={(v) => {
                      const id = v ? Number(v) : undefined;
                      const dev = claimableDevices.find((d) => d.id === id);
                      const plats = dev?.accounts?.map((a) => a.platform) ?? [
                        "douyin",
                      ];
                      setForm({
                        ...form,
                        target_device_id: id,
                        platform: plats.includes(form.platform ?? "douyin")
                          ? form.platform
                          : plats[0],
                      });
                    }}
                  />
                </label>
                <label className="field">
                  <span>发布平台 · 必选</span>
                  <Select
                    variant="field"
                    value={form.platform ?? "douyin"}
                    options={(
                      claimableDevices
                        .find((d) => d.id === form.target_device_id)
                        ?.accounts?.map((a) => a.platform) ?? ["douyin"]
                    ).map((pl) => ({
                      value: pl,
                      label: pl === "xhs" ? "小红书" : "抖音",
                    }))}
                    onChange={(v) => setForm({ ...form, platform: v })}
                  />
                </label>
                <label className="field">
                  <span>定时发布（不设置就立即发布）</span>
                  {schedule ? (
                    <DateTime24 value={schedule} min={nowLocal()} onChange={setSchedule} />
                  ) : (
                    <button
                      type="button"
                      className="button secondary"
                      onClick={() => setSchedule(nowLocal())}
                    >
                      设为定时
                    </button>
                  )}
                  {schedule ? (
                    <button
                      type="button"
                      className="link-clear"
                      onClick={() => setSchedule("")}
                    >
                      改为立即发布
                    </button>
                  ) : null}
                </label>
              </div>
            </div>

            {error ? <p className="form-error">{error}</p> : null}
            <footer className="modal-footer">
              <span>
                <Check size={14} />
                {schedule ? "到点后由所选设备自动发布" : "提交后所选设备立即开始发布"}
              </span>
              <div>
                <button
                  type="button"
                  className="button secondary"
                  onClick={onClose}
                >
                  取消
                </button>
                <button className="button primary" disabled={submitting}>
                  {submitting ? "正在提交…" : "提交发布任务"}
                  <ChevronRight size={16} />
                </button>
              </div>
            </footer>
          </form>
        </div>
      </section>
    </div>
  );
}
