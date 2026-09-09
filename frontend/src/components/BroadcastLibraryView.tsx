import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  AtSign,
  Check,
  Image as ImageIcon,
  Pencil,
  Plus,
  Send,
  Trash2,
  X,
} from "lucide-react";

import {
  createBroadcastMessage,
  deleteBroadcastMessage,
  deleteImage,
  fileUrl,
  getBroadcastSchedule,
  listBroadcastMessages,
  listImages,
  updateBroadcastMessage,
  updateImage,
  uploadImage,
} from "../lib/api";
import type {
  BroadcastMessage,
  BroadcastSchedule,
  Device,
  ImageAsset,
} from "../lib/types";
import { ImagePickerModal } from "./ImagePickerModal";
import { Select } from "./Select";
import { useConfirm } from "./ConfirmModal";
import { Pager, usePaged } from "./Pager";

/** 「通用」这类值代表"所有号都能用"，不是一个真实城市。后端 `_usable_messages`
 *  认的就是这两个词，前端跟着它走，别自己另立一套。 */
const GENERIC_CITIES = ["通用", "全国"];
const isGeneric = (city: string) => !city || GENERIC_CITIES.includes(city);

/** ⚠ 这个计数是在**排程时**加的，不是发出去之后才加的（见 auto_broadcast
 *  的 plan_for_account）。所以它的准确说法是「这条被用过几次」——
 *  写成「已推 N 次」的话，被取消或过期的那几次也算进去了，数字对不上群里的实际。
 *  轮换挑消息（用得少的优先）本来也就是按这个计数走的。 */
function timeAgo(iso?: string | null) {
  if (!iso) return "还没用过";
  const then = new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "刚刚用过";
  if (mins < 60) return `${mins} 分钟前用过`;
  if (mins < 60 * 24) return `${Math.floor(mins / 60)} 小时前用过`;
  return `${Math.floor(mins / 1440)} 天前用过`;
}

interface Draft {
  id?: number;
  text: string;
  imageId?: number;
  mentionAll: boolean;
  city: string;
  enabled: boolean;
}

const EMPTY: Draft = {
  text: "",
  mentionAll: false,
  city: "通用",
  enabled: true,
};

interface Props {
  devices: Device[];
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
}

/**
 * 群推送消息库。
 *
 * 这是「群推送」这条线的**库**，和内容发布线的「内容库」一个位置。两者最大的区别：
 * 作品发一次就消费掉了，**群消息可以反复用** —— 所以这里是标准的增删改查，
 * 没有"待发/已发"两栏。
 *
 * 为什么必须有这个页面：自动推送就是从这个库里挑消息的。在它之前，往库里加一条
 * 只能 curl 打 `/broadcast-library`，等于自动推送这个功能对运营根本不存在。
 */
export function BroadcastLibraryView({ devices, onNotice }: Props) {
  const [rows, setRows] = useState<BroadcastMessage[]>([]);
  const [images, setImages] = useState<ImageAsset[]>([]);
  const [schedule, setSchedule] = useState<BroadcastSchedule>();
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<Draft>();
  const [cityFilter, setCityFilter] = useState("all");
  const [stateFilter, setStateFilter] = useState("all");
  const [confirm, confirmUI] = useConfirm();

  async function reload() {
    try {
      const [list, schedules] = await Promise.all([
        listBroadcastMessages(),
        getBroadcastSchedule().catch(() => undefined),
      ]);
      setRows(list);
      setSchedule(schedules);
    } catch (error) {
      onNotice("error", "读取消息库失败", error instanceof Error ? error.message : "");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    listImages().then(setImages).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 城市候选 = 账号实际所在的城市 ∪ 库里已经用过的城市。
  // 只列前者会让历史数据里的城市选不回来，只列后者则新城市永远加不进去。
  const cities = useMemo(() => {
    const set = new Set<string>();
    devices.forEach((d) => {
      const c = (d.city || "").trim();
      if (c && !isGeneric(c)) set.add(c);
    });
    rows.forEach((m) => {
      const c = (m.city || "").trim();
      if (c && !isGeneric(c)) set.add(c);
    });
    return [...set].sort();
  }, [devices, rows]);

  const shown = rows.filter((m) => {
    if (cityFilter === "generic" && !isGeneric(m.city)) return false;
    if (cityFilter !== "all" && cityFilter !== "generic" && m.city !== cityFilter)
      return false;
    if (stateFilter === "on" && !m.enabled) return false;
    if (stateFilter === "off" && m.enabled) return false;
    return true;
  });
  const page = usePaged(shown, 12);

  const usable = rows.filter((m) => m.enabled).length;
  const totalSent = rows.reduce((sum, m) => sum + m.sent_count, 0);

  function toPayload(d: Draft) {
    return {
      text: d.text.trim(),
      image_id: d.imageId ?? null,
      mention_all: d.mentionAll,
      city: d.city.trim() || "通用",
      enabled: d.enabled,
    };
  }

  async function save(d: Draft) {
    if (!d.text.trim()) {
      onNotice("error", "保存失败", "消息内容不能为空");
      return;
    }
    try {
      if (d.id) await updateBroadcastMessage(d.id, toPayload(d));
      else await createBroadcastMessage(toPayload(d));
      setDraft(undefined);
      await reload();
      onNotice(
        "success",
        d.id ? "已保存" : "已加入消息库",
        d.enabled
          ? "自动推送会从启用的消息里挑一条发到群"
          : "这条是停用状态，自动推送不会挑到它",
      );
    } catch (error) {
      onNotice("error", "保存失败", error instanceof Error ? error.message : "");
    }
  }

  async function toggle(m: BroadcastMessage) {
    try {
      await updateBroadcastMessage(m.id, {
        text: m.text,
        // ⚠ 必须原样带上：image_id 为空的语义是「这条不带图」，
        // 漏传会让"点一下停用"顺手把图片删了
        image_id: m.image_id ?? null,
        mention_all: m.mention_all,
        city: m.city,
        enabled: !m.enabled,
      });
      await reload();
    } catch (error) {
      onNotice("error", "操作失败", error instanceof Error ? error.message : "");
    }
  }

  async function remove(m: BroadcastMessage) {
    const ok = await confirm({
      title: "删除这条群推送消息？",
      detail:
        m.sent_count > 0
          ? `它已经被用过 ${m.sent_count} 次。删除只影响以后，已经发进群里的消息撤不回来。`
          : "删除后自动推送不会再挑到它。",
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteBroadcastMessage(m.id);
      await reload();
      onNotice("success", "已删除", "这条消息已从库里移除");
    } catch (error) {
      onNotice("error", "删除失败", error instanceof Error ? error.message : "");
    }
  }

  return (
    <div className="content-page page-enter">
      <header className="catalog-hero broadcast-hero">
        <div>
          <span>群推送</span>
          <h2>常用的群消息，存一次，用很久。</h2>
          <p>
            自动推送每天从这里挑一条发到群。填了城市的只发给该城市的账号，
            填「通用」的所有账号都能用。
          </p>
        </div>
        <div className="hero-stats">
          <div className="hero-stat accent">
            <strong>{usable}</strong>
            <small>启用中</small>
          </div>
          <div className="hero-stat">
            <strong>{rows.length - usable}</strong>
            <small>已停用</small>
          </div>
          <div className="hero-stat">
            <strong>{totalSent}</strong>
            <small>累计推送</small>
          </div>
        </div>
      </header>

      {/* 库空着而自动推送开着 = 每天到点什么都发不出去，而且界面上一点异常都看不到。
          这是这个页面最该主动说的一件事。 */}
      {schedule && schedule.daily_count > 0 && !usable ? (
        <p className="library-warn">
          自动推送开着（每天 {schedule.daily_count} 次，{schedule.windows_readable}），
          但库里没有一条启用的消息 —— 到点会没东西可发。
        </p>
      ) : null}

      <div className="content-toolbar">
        <button className="button primary" onClick={() => setDraft({ ...EMPTY })}>
          <Plus size={15} /> 新增消息
        </button>
        <Select
          label="城市"
          value={cityFilter}
          options={[
            { value: "all", label: "全部", count: rows.length },
            {
              value: "generic",
              label: "通用",
              count: rows.filter((m) => isGeneric(m.city)).length,
            },
            ...cities.map((c) => ({
              value: c,
              label: c,
              count: rows.filter((m) => m.city === c).length,
            })),
          ]}
          onChange={setCityFilter}
        />
        <Select
          label="状态"
          value={stateFilter}
          options={[
            { value: "all", label: "全部" },
            { value: "on", label: "启用中", count: usable },
            { value: "off", label: "已停用", count: rows.length - usable },
          ]}
          onChange={setStateFilter}
        />
      </div>

      <div className="msg-grid">
        {loading ? (
          <p className="content-block-empty">正在读取消息库…</p>
        ) : !shown.length ? (
          <p className="content-block-empty">
            {rows.length
              ? "没有符合筛选条件的消息。"
              : "消息库还是空的。点上方「新增消息」加第一条 —— 自动推送就是从这里挑消息发的。"}
          </p>
        ) : (
          page.slice.map((m) => (
            <article
              key={m.id}
              className={`msg-card ${m.enabled ? "" : "off"}`}
            >
              <div className="msg-card-top">
                <span
                  className={`content-city ${isGeneric(m.city) ? "generic" : ""}`}
                >
                  {isGeneric(m.city) ? "通用" : m.city}
                </span>
                {m.mention_all ? (
                  <span className="msg-chip">
                    <AtSign size={11} /> @所有人
                  </span>
                ) : null}
                {!m.enabled ? <span className="msg-chip off">已停用</span> : null}
              </div>

              <p className="msg-card-text">{m.text}</p>

              {m.image_url ? (
                <div className="msg-card-img">
                  <img src={fileUrl(m.image_url)} alt="" />
                  <small>
                    <ImageIcon size={11} /> 带图发送
                  </small>
                </div>
              ) : null}

              <footer className="msg-card-foot">
                <small>
                  <Send size={11} /> 用过 {m.sent_count} 次 · {timeAgo(m.last_sent_at)}
                </small>
                <div className="msg-card-actions">
                  <button
                    className="ghost-icon"
                    title={m.enabled ? "停用" : "启用"}
                    onClick={() => void toggle(m)}
                  >
                    {m.enabled ? <X size={15} /> : <Check size={15} />}
                  </button>
                  <button
                    className="ghost-icon"
                    title="编辑"
                    onClick={() =>
                      setDraft({
                        id: m.id,
                        text: m.text,
                        imageId: m.image_id ?? undefined,
                        mentionAll: m.mention_all,
                        city: isGeneric(m.city) ? "通用" : m.city,
                        enabled: m.enabled,
                      })
                    }
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    className="ghost-icon danger"
                    title="删除"
                    onClick={() => void remove(m)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </footer>
            </article>
          ))
        )}
      </div>
      <Pager
        page={page.page}
        pageCount={page.pageCount}
        total={page.total}
        onChange={page.setPage}
      />

      {draft ? (
        <MessageEditor
          draft={draft}
          cities={cities}
          images={images}
          onImagesChange={setImages}
          onNotice={onNotice}
          onChange={setDraft}
          onClose={() => setDraft(undefined)}
          onSave={() => void save(draft)}
        />
      ) : null}
      {confirmUI}
    </div>
  );
}

function MessageEditor({
  draft,
  cities,
  images,
  onImagesChange,
  onNotice,
  onChange,
  onClose,
  onSave,
}: {
  draft: Draft;
  cities: string[];
  images: ImageAsset[];
  onImagesChange: (rows: ImageAsset[]) => void;
  onNotice: (tone: "success" | "error", title: string, message: string) => void;
  onChange: (d: Draft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const shownImage = images.find((i) => i.id === draft.imageId);

  return createPortal(
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="schedule-modal"
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="schedule-head">
          <div>
            <strong>{draft.id ? "编辑群推送消息" : "新增群推送消息"}</strong>
            <small>自动推送每天从启用的消息里挑一条发到群</small>
          </div>
          <button className="ghost-icon" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="schedule-body">
          <label className="field">
            <span>消息内容</span>
            <textarea
              rows={6}
              autoFocus
              value={draft.text}
              placeholder="输入要发到群里的文字"
              onChange={(e) => onChange({ ...draft, text: e.target.value })}
            />
            <small>{draft.text.trim().length} 字</small>
          </label>

          <label className="field">
            <span>适用城市</span>
            <Select
              variant="field"
              value={draft.city}
              creatable
              options={[
                { value: "通用", label: "通用（所有账号都能用）" },
                ...cities.map((c) => ({ value: c, label: c })),
              ]}
              onChange={(v) => onChange({ ...draft, city: v })}
            />
            <small className="field-note">
              填了具体城市，就只有该城市的账号会推这条；本城消息优先于通用消息。
            </small>
          </label>

          <div className="field">
            <span>
              <ImageIcon size={12} /> 图片（可选）
            </span>
            <div className="image-trigger-row">
              {shownImage ? (
                <button
                  type="button"
                  className="image-trigger has-img"
                  onClick={() => setPickerOpen(true)}
                >
                  <img src={shownImage.url} alt={shownImage.title} />
                  <span>
                    <strong>{shownImage.title}</strong>
                    <small>{shownImage.category} · 点击更换</small>
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
              {shownImage ? (
                <button
                  type="button"
                  className="image-clear"
                  onClick={() => onChange({ ...draft, imageId: undefined })}
                >
                  不发图片
                </button>
              ) : null}
            </div>
          </div>

          <label className="msg-check">
            <input
              type="checkbox"
              checked={draft.mentionAll}
              onChange={(e) => onChange({ ...draft, mentionAll: e.target.checked })}
            />
            <span>
              <AtSign size={13} /> 发送时 @所有人
            </span>
          </label>
          <label className="msg-check">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
            />
            <span>启用（自动推送可以挑到这条）</span>
          </label>
        </div>

        <footer className="schedule-foot">
          <button className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" onClick={onSave}>
            <Check size={15} /> 保存
          </button>
        </footer>
      </section>

      {pickerOpen ? (
        <ImagePickerModal
          images={images}
          selectedId={draft.imageId}
          uploading={uploading}
          onSelect={(id) => {
            onChange({ ...draft, imageId: id });
            setPickerOpen(false);
          }}
          onUpload={async (file, meta) => {
            setUploading(true);
            try {
              const img = await uploadImage(file, meta);
              onImagesChange([img, ...images]);
              onChange({ ...draft, imageId: img.id });
            } catch (error) {
              onNotice("error", "上传失败", error instanceof Error ? error.message : "");
            } finally {
              setUploading(false);
            }
          }}
          onRename={async (img, meta) => {
            try {
              const next = await updateImage(img.id, meta);
              onImagesChange(images.map((i) => (i.id === next.id ? next : i)));
            } catch (error) {
              onNotice("error", "改名失败", error instanceof Error ? error.message : "");
            }
          }}
          onDelete={async (img) => {
            try {
              await deleteImage(img.id);
              onImagesChange(images.filter((i) => i.id !== img.id));
              if (draft.imageId === img.id) onChange({ ...draft, imageId: undefined });
            } catch (error) {
              onNotice("error", "删除失败", error instanceof Error ? error.message : "");
            }
          }}
          onClose={() => setPickerOpen(false)}
        />
      ) : null}
    </div>,
    document.body,
  );
}
