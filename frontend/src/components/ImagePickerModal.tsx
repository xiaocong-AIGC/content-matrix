import { useState } from "react";
import { createPortal } from "react-dom";
import { Check, Pencil, Search, Trash2, Upload, X } from "lucide-react";

import { fileUrl } from "../lib/api";
import type { ImageAsset } from "../lib/types";
import { FormModal } from "./FormModal";

import { useConfirm } from "./ConfirmModal";
interface Props {
  images: ImageAsset[];
  selectedId?: number;
  uploading: boolean;
  onSelect: (id: number | undefined) => void;
  onUpload: (file: File, meta: { title: string; category: string }) => void;
  onRename: (img: ImageAsset, meta: { title: string; category: string }) => void;
  onDelete: (img: ImageAsset) => void;
  onClose: () => void;
}

export function ImagePickerModal({
  images,
  selectedId,
  uploading,
  onSelect,
  onUpload,
  onRename,
  onDelete,
  onClose,
}: Props) {
  const [confirm, confirmUI] = useConfirm();
  const [filter, setFilter] = useState("全部");
  const [enlarge, setEnlarge] = useState<ImageAsset>();
  const [pendingFile, setPendingFile] = useState<File>();
  const [editing, setEditing] = useState<ImageAsset>();

  const categories = ["全部", ...new Set(images.map((i) => i.category))];
  const shown =
    filter === "全部" ? images : images.filter((i) => i.category === filter);
  const suggestions = [...new Set(images.map((i) => i.category))];

  return createPortal(
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      {confirmUI}
      <section
        className="schedule-modal image-modal"
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="schedule-head">
          <div>
            <strong>选择图片</strong>
            <small>共 {images.length} 张 · 选一张用于群发，点放大镜看大图</small>
          </div>
          <button className="ghost-icon" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="schedule-body">
          {categories.length > 1 ? (
            <div className="image-cats">
              {categories.map((c) => (
                <button
                  key={c}
                  className={`image-cat ${filter === c ? "on" : ""}`}
                  onClick={() => setFilter(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          ) : null}

          <div className="image-grid">
            <label className="image-grid-upload">
              <input
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) setPendingFile(file);
                }}
              />
              <Upload size={18} />
              <span>{uploading ? "上传中…" : "上传图片"}</span>
            </label>

            {shown.map((img) => (
              <div
                key={img.id}
                className={`image-tile ${selectedId === img.id ? "on" : ""}`}
              >
                <button
                  type="button"
                  className="image-tile-pick"
                  onClick={() => onSelect(img.id)}
                >
                  <img src={fileUrl(img.url)} alt={img.title} />
                  {selectedId === img.id ? (
                    <span className="image-tile-check">
                      <Check size={14} />
                    </span>
                  ) : null}
                </button>
                <div className="image-tile-bar">
                  <span className="image-tile-name" title={img.title}>
                    {img.title}
                  </span>
                  <button
                    type="button"
                    aria-label="看大图"
                    onClick={() => setEnlarge(img)}
                  >
                    <Search size={13} />
                  </button>
                  <button
                    type="button"
                    aria-label="重命名"
                    onClick={() => setEditing(img)}
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    type="button"
                    aria-label="删除"
                    className="image-tile-del"
                    onClick={async () => {
                      if (
                        await confirm({
                          title: `删除图片「${img.title}」？`,
                          confirmText: "删除",
                          danger: true,
                        })
                      ) {
                        onDelete(img);
                      }
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <span className="image-tile-cat">{img.category}</span>
              </div>
            ))}
          </div>
        </div>

        <footer className="schedule-foot">
          <button className="button secondary" onClick={onClose}>
            取消
          </button>
          <button
            className="button secondary"
            onClick={() => {
              onSelect(undefined);
              onClose();
            }}
          >
            不发图片
          </button>
          <button
            className="button primary"
            disabled={!selectedId}
            onClick={onClose}
          >
            <Check size={15} /> 使用这张
          </button>
        </footer>
      </section>

      {enlarge ? (
        <div
          className="image-lightbox"
          role="presentation"
          onMouseDown={(e) => {
            e.stopPropagation();
            setEnlarge(undefined);
          }}
        >
          <img src={fileUrl(enlarge.url)} alt={enlarge.title} />
          <span className="image-lightbox-cap">
            {enlarge.title} · {enlarge.category}
          </span>
        </div>
      ) : null}

      {pendingFile ? (
        <FormModal
          title="上传图片"
          subtitle={pendingFile.name}
          confirmText="上传"
          fields={[
            {
              name: "title",
              label: "图片名称",
              value: pendingFile.name.replace(/\.[^.]+$/, ""),
            },
            {
              name: "category",
              label: "分类",
              value: filter === "全部" ? "未分类" : filter,
              placeholder: "如 海报、二维码、活动",
              suggestions,
            },
          ]}
          onConfirm={(v) => {
            const file = pendingFile;
            setPendingFile(undefined);
            onUpload(file, {
              title: v.title.trim(),
              category: v.category.trim() || "未分类",
            });
          }}
          onClose={() => setPendingFile(undefined)}
        />
      ) : null}

      {editing ? (
        <FormModal
          title="重命名 / 改分类"
          fields={[
            { name: "title", label: "图片名称", value: editing.title },
            {
              name: "category",
              label: "分类",
              value: editing.category,
              suggestions,
            },
          ]}
          onConfirm={(v) => {
            const img = editing;
            setEditing(undefined);
            onRename(img, {
              title: v.title.trim(),
              category: v.category.trim() || "未分类",
            });
          }}
          onClose={() => setEditing(undefined)}
        />
      ) : null}
    </div>,
    document.body,
  );
}
