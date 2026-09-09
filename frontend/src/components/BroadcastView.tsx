import { useEffect, useState } from "react";
import {
  AtSign,
  CalendarClock,
  Image as ImageIcon,
  Megaphone,
  RefreshCw,
  Send,
  Users,
} from "lucide-react";

import {
  createBroadcast,
  deleteImage,
  fileUrl,
  getTask,
  listGroups,
  listImages,
  refreshAccount,
  updateImage,
  uploadImage,
} from "../lib/api";
import type { ChatGroup, Device, ImageAsset, PublishTask } from "../lib/types";
import { ImagePickerModal } from "./ImagePickerModal";
import { Select } from "./Select";
import { Pager, usePaged } from "./Pager";
import { DateTime24 } from "./DateTime24";
import { StatusBadge } from "./StatusBadge";

interface Props {
  devices: Device[];
  tasks: PublishTask[];
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
}

export function BroadcastView({ devices, tasks, onNotice }: Props) {
  const [deviceId, setDeviceId] = useState<number | undefined>(devices[0]?.id);
  const [groups, setGroups] = useState<ChatGroup[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [mentionAll, setMentionAll] = useState(false);
  const [images, setImages] = useState<ImageAsset[]>([]);
  const [imageId, setImageId] = useState<number>();
  const [uploading, setUploading] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [schedule, setSchedule] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  // 「重新扫描群」— 复用「更新账号」(它现在会同时重扫群)，然后在设备扫描的
  // 这段时间里轮询几次群列表，扫到就自动刷新（离线设备后端会直接拒绝）。
  async function rescanGroups() {
    if (!deviceId || rescanning) return;
    setRescanning(true);
    try {
      await refreshAccount(deviceId);
      onNotice(
        "success",
        "正在重新扫描群",
        "约 15–30 秒后群列表会自动刷新",
      );
      const target = deviceId;
      let n = 0;
      const timer = window.setInterval(async () => {
        n += 1;
        try {
          if (deviceId === target) setGroups(await listGroups(target));
        } catch {
          /* transient — keep polling */
        }
        if (n >= 6) window.clearInterval(timer);
      }, 6000);
    } catch (error) {
      onNotice("error", "重新扫描失败", error instanceof Error ? error.message : "");
    } finally {
      setTimeout(() => setRescanning(false), 3000);
    }
  }

  useEffect(() => {
    if (!deviceId) {
      setGroups([]);
      return;
    }
    setSelected([]);
    listGroups(deviceId)
      .then(setGroups)
      .catch(() => setGroups([]));
  }, [deviceId]);

  useEffect(() => {
    listImages()
      .then(setImages)
      .catch(() => setImages([]));
  }, []);

  async function onUpload(
    file: File,
    meta: { title: string; category: string },
  ) {
    setUploading(true);
    try {
      const asset = await uploadImage(file, meta);
      setImages((prev) => [asset, ...prev]);
      setImageId(asset.id);
    } catch (error) {
      onNotice("error", "图片上传失败", error instanceof Error ? error.message : "");
    } finally {
      setUploading(false);
    }
  }

  async function renameImage(
    img: ImageAsset,
    meta: { title: string; category: string },
  ) {
    try {
      const updated = await updateImage(img.id, meta);
      setImages((prev) => prev.map((x) => (x.id === img.id ? updated : x)));
    } catch (error) {
      onNotice("error", "修改失败", error instanceof Error ? error.message : "");
    }
  }

  async function removeImage(img: ImageAsset) {
    try {
      await deleteImage(img.id);
      setImages((prev) => prev.filter((x) => x.id !== img.id));
      if (imageId === img.id) setImageId(undefined);
    } catch (error) {
      onNotice("error", "删除失败", error instanceof Error ? error.message : "");
    }
  }

  const selectedImage = images.find((i) => i.id === imageId);
  // datetime-local lower bound: now (can't schedule a send in the past).
  const minDateTime = (() => {
    const p = (n: number) => String(n).padStart(2, "0");
    const d = new Date();
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(
      d.getHours(),
    )}:${p(d.getMinutes())}`;
  })();

  const [openId, setOpenId] = useState<number>();
  const [detail, setDetail] = useState<PublishTask>();

  // 记录区默认看**全部账号**。以前硬编码只看左边选中的那一个 ——
  // 自动推送产生的记录分布在十几台设备上，于是整批看不见，
  // 「今天推成功了吗」在界面上无解。
  const [recScope, setRecScope] = useState("all");     // all | 某个 device id
  const [recSource, setRecSource] = useState("all");   // all | auto | manual
  const [recStatus, setRecStatus] = useState("all");   // all | succeeded | failed

  const allRecords = tasks
    .filter((t) => t.publish_type === "group_message")
    .sort((a, b) => b.id - a.id);
  const records = allRecords.filter((t) => {
    if (recScope !== "all" && String(t.target_device_id) !== recScope) return false;
    // 自动排的任务名字带「群推送：」前缀，手动发的是「群发：」——
    // 后端就是靠这个前缀区分要不要作废过期的那些，前端沿用同一个判据
    const auto = (t.name ?? "").startsWith("群推送：");
    if (recSource === "auto" && !auto) return false;
    if (recSource === "manual" && auto) return false;
    if (recStatus === "succeeded" && t.status !== "succeeded") return false;
    if (recStatus === "failed" && t.status !== "failed") return false;
    return true;
  });
  const recordsPage = usePaged(records, 10);
  const autoCount = allRecords.filter((t) =>
    (t.name ?? "").startsWith("群推送："),
  ).length;

  async function openRecord(id: number) {
    if (openId === id) {
      setOpenId(undefined);
      return;
    }
    setOpenId(id);
    try {
      setDetail(await getTask(id));
    } catch {
      setDetail(undefined);
    }
  }

  function toggleGroup(name: string) {
    setSelected((s) =>
      s.includes(name) ? s.filter((x) => x !== name) : [...s, name],
    );
  }

  async function submit() {
    if (!deviceId) return onNotice("error", "无法发送", "请先选择账号");
    if (!selected.length) return onNotice("error", "无法发送", "请至少选择一个群");
    if (!text.trim()) return onNotice("error", "无法发送", "请输入消息文字");
    setSubmitting(true);
    try {
      await createBroadcast({
        target_device_id: deviceId,
        text,
        image_id: imageId ?? null,
        mention_all: mentionAll,
        groups: selected,
        scheduled_at: schedule ? new Date(schedule).toISOString() : null,
      });
      onNotice(
        "success",
        "群发任务已创建",
        schedule
          ? "到点后该账号将自动群发到所选群"
          : "该账号将开始群发到所选群",
      );
      setText("");
      setImageId(undefined);
      setSelected([]);
      setMentionAll(false);
      setSchedule("");
    } catch (error) {
      onNotice("error", "创建失败", error instanceof Error ? error.message : "");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="catalog-page page-enter">
      <header className="catalog-hero broadcast-hero">
        <div>
          <span>群推送</span>
          <h2>现在就发一条到群里。</h2>
          <p>
            选好账号和群，可以带图，可以 @所有人，也可以约定一个时间再发。
            只会发到你自己管理的群；同一个群不建议发得太频繁。
          </p>
        </div>
        <Megaphone size={64} strokeWidth={0.9} />
      </header>

      <div className="broadcast-layout">
        <section className="broadcast-compose">
          <label className="field">
            <span>发送账号</span>
            <Select
              variant="field"
              value={deviceId ? String(deviceId) : ""}
              placeholder="选一个账号"
              options={devices.map((d) => ({
                value: String(d.id),
                label:
                  (d.douyin_nickname || d.name) +
                  (d.douyin_id ? `（抖音号 ${d.douyin_id}）` : ""),
              }))}
              onChange={(v) => setDeviceId(v ? Number(v) : undefined)}
            />
          </label>

          <label className="field">
            <span>消息文字</span>
            <textarea
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="输入要群发的文字内容"
            />
          </label>

          <div className="field">
            <span>
              <ImageIcon size={12} /> 图片（可选）
            </span>
            <div className="image-trigger-row">
              {selectedImage ? (
                <button
                  type="button"
                  className="image-trigger has-img"
                  onClick={() => setPickerOpen(true)}
                >
                  <img src={selectedImage.url} alt={selectedImage.title} />
                  <span>
                    <strong>{selectedImage.title}</strong>
                    <small>{selectedImage.category} · 点击更换</small>
                  </span>
                </button>
              ) : (
                <button
                  type="button"
                  className="image-trigger"
                  onClick={() => setPickerOpen(true)}
                >
                  <ImageIcon size={16} /> 从图片库选择 / 上传
                </button>
              )}
              {selectedImage ? (
                <button
                  type="button"
                  className="image-clear"
                  onClick={() => setImageId(undefined)}
                >
                  不发图片
                </button>
              ) : null}
            </div>
          </div>

          <label className="broadcast-check">
            <input
              type="checkbox"
              checked={mentionAll}
              onChange={(e) => setMentionAll(e.target.checked)}
            />
            <AtSign size={14} /> @所有人（仅在你是群主/管理员的群生效）
          </label>

          <label className="field">
            <span>
              <CalendarClock size={12} /> 定时发送（不设置就立即发送）
            </span>
            {schedule ? (
              <DateTime24 value={schedule} min={minDateTime} onChange={setSchedule} />
            ) : (
              <button
                type="button"
                className="button secondary"
                onClick={() => setSchedule(minDateTime)}
              >
                选择发送时间
              </button>
            )}
            {schedule ? (
              <button type="button" className="link-clear" onClick={() => setSchedule("")}>
                改为立即发送
              </button>
            ) : null}
          </label>

          <button
            className="button primary broadcast-submit"
            disabled={submitting}
            onClick={() => void submit()}
          >
            <Send size={15} />
            {submitting ? "正在创建…" : `群发到 ${selected.length} 个群`}
          </button>
        </section>

        <section className="broadcast-groups">
          <header className="section-heading">
            <div>
              <span>选择群</span>
              <h2>
                <Users size={16} /> 可发送的群
              </h2>
            </div>
            <div className="broadcast-groups-actions">
              <button
                type="button"
                className="detect-btn"
                onClick={() => void rescanGroups()}
                disabled={!deviceId || rescanning}
                title="重新扫描这个账号的群（新建群或退群后用）"
              >
                <RefreshCw size={14} className={rescanning ? "spin" : ""} />
                {rescanning ? "扫描中…" : "重新扫描群"}
              </button>
              <small>{groups.length} 个</small>
            </div>
          </header>
          {groups.length ? (
            <div className="group-list">
              {groups.map((g) => (
                <label
                  key={g.id}
                  className={`group-row ${selected.includes(g.group_name) ? "on" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(g.group_name)}
                    onChange={() => toggleGroup(g.group_name)}
                  />
                  <div>
                    <strong>{g.group_name}</strong>
                  </div>
                </label>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-number">00</span>
              <strong>暂无群列表</strong>
              <p>在手机上启动 Agent 后，会自动读取该账号的群并显示在这里。</p>
            </div>
          )}
        </section>
      </div>

      <section className="broadcast-records">
        <header className="section-heading">
          <div>
            <span>群发记录</span>
            <h2>最近群发</h2>
          </div>
          <div className="record-filters">
            <Select
              label="账号"
              value={recScope}
              options={[
                { value: "all", label: "全部账号" },
                ...devices
                  .filter((d) => d.douyin_nickname || d.name)
                  .map((d) => ({
                    value: String(d.id),
                    label: d.douyin_nickname || d.name,
                  })),
              ]}
              onChange={setRecScope}
            />
            <Select
              label="来源"
              value={recSource}
              options={[
                { value: "all", label: "全部" },
                { value: "auto", label: `自动推送${autoCount ? ` ${autoCount}` : ""}` },
                { value: "manual", label: "手动发送" },
              ]}
              onChange={setRecSource}
            />
            <Select
              label="结果"
              value={recStatus}
              options={[
                { value: "all", label: "全部" },
                { value: "succeeded", label: "成功" },
                { value: "failed", label: "失败" },
              ]}
              onChange={setRecStatus}
            />
            <small>{records.length} 条</small>
          </div>
        </header>
        {records.length ? (
          <div className="record-list">
            {recordsPage.slice.map((t) => (
              <div key={t.id} className="record-item">
                <button className="record-row" onClick={() => void openRecord(t.id)}>
                  <div>
                    <strong>{t.body || t.name}</strong>
                    <small>
                      {(() => {
                        const d = devices.find((x) => x.id === t.target_device_id);
                        return d ? `${d.douyin_nickname || d.name} · ` : "";
                      })()}
                      {(t.name ?? "").startsWith("群推送：") ? "自动 · " : ""}
                      发往 {(t.target_groups ?? []).join("、") || "—"} ·{" "}
                      <b className="record-time">
                        {new Intl.DateTimeFormat("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        }).format(new Date(t.finished_at ?? t.created_at))}
                      </b>
                      {t.mention_all ? " · @所有人" : ""}
                    </small>
                    {t.error_message ? (
                      <small className="record-err">{t.error_message}</small>
                    ) : null}
                  </div>
                  <StatusBadge status={t.status} />
                </button>
                {openId === t.id && detail?.id === t.id ? (
                  <div className="record-detail">
                    {detail.screenshots?.length ? (
                      <div className="record-shots">
                        {detail.screenshots.map((s) => (
                          <a
                            key={s.id}
                            href={fileUrl(`/api/v1/agent/screenshots/${s.id}/file`)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <img
                              src={fileUrl(`/api/v1/agent/screenshots/${s.id}/file`)}
                              alt={s.step}
                            />
                          </a>
                        ))}
                      </div>
                    ) : (
                      <p className="record-noshot">这次没有截图（没开录屏权限，或者发送太快）。</p>
                    )}
                    <div className="record-logs">
                      {[...(detail.logs ?? [])].reverse().map((l) => (
                        <div key={l.id} className={`record-log ${l.level}`}>
                          <time>
                            {new Intl.DateTimeFormat("zh-CN", {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                            }).format(new Date(l.created_at))}
                          </time>
                          <span>{l.message}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="record-empty">没有找到群发记录。</p>
        )}
        <Pager
          page={recordsPage.page}
          pageCount={recordsPage.pageCount}
          total={recordsPage.total}
          onChange={recordsPage.setPage}
        />
      </section>

      {pickerOpen ? (
        <ImagePickerModal
          images={images}
          selectedId={imageId}
          uploading={uploading}
          onSelect={(id) => setImageId(id)}
          onUpload={(file, meta) => void onUpload(file, meta)}
          onRename={(img, meta) => void renameImage(img, meta)}
          onDelete={(img) => void removeImage(img)}
          onClose={() => setPickerOpen(false)}
        />
      ) : null}
    </div>
  );
}
