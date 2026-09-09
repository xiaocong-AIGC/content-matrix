import { useCallback, useState } from "react";
import { createPortal } from "react-dom";
import { Check, X } from "lucide-react";

import { Select } from "./Select";

export interface FormField {
  name: string;
  label: string;
  value: string;
  placeholder?: string;
  type?: "text" | "textarea" | "select";
  /** 候选值**带显示名**。给了它就用它 —— `suggestions` 是把值直接当文字显示的，
   *  枚举字段（平台、角色这种）用那个会把 `douyin` `xhs` 原样打到屏幕上。 */
  options?: { value: string; label: string }[];
  /** 候选值。**给了候选就一定走统一的 Select**（可搜索、可新增）——
   *  以前这里会退回原生 datalist，那是条静默降级的路：同一个「选城市」的动作，
   *  有的页面能搜能新建、有的只弹个浏览器自己画的黑气泡，用户完全不知道为什么。 */
  suggestions?: string[];
  /** select 专用：允许输入候选里没有的新值。城市这类开放集合要打开它 ——
   *  纯枚举意味着每加一个城市都要改代码，纯输入框又会打出「上海市」「上海 」
   *  这种和已有值差一点的脏值，而城市是内容分发的键，脏一个就意味着那条内容
   *  谁都取不到。 */
  creatable?: boolean;
}

interface Props {
  title: string;
  subtitle?: string;
  fields: FormField[];
  confirmText?: string;
  onConfirm: (values: Record<string, string>) => void;
  onClose: () => void;
}

/** Styled in-app replacement for window.prompt — matches the schedule modal. */
export function FormModal({
  title,
  subtitle,
  fields,
  confirmText = "确定",
  onConfirm,
  onClose,
}: Props) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(fields.map((f) => [f.name, f.value])),
  );

  function set(name: string, v: string) {
    setValues((prev) => ({ ...prev, [name]: v }));
  }

  function submit() {
    onConfirm(values);
  }

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
            <strong>{title}</strong>
            {subtitle ? <small>{subtitle}</small> : null}
          </div>
          <button className="ghost-icon" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="schedule-body">
          {fields.map((field) => {
            return (
              <label className="field" key={field.name}>
                <span>{field.label}</span>
                {field.type === "select" ||
                field.options?.length ||
                (field.suggestions?.length && field.type !== "textarea") ? (
                  <Select
                    variant="field"
                    value={values[field.name] ?? ""}
                    placeholder={field.placeholder ?? "请选择"}
                    creatable={field.creatable}
                    options={
                      field.options ??
                      (field.suggestions ?? []).map((v) => ({ value: v, label: v }))
                    }
                    onChange={(v) => set(field.name, v)}
                  />
                ) : field.type === "textarea" ? (
                  <textarea
                    rows={3}
                    value={values[field.name] ?? ""}
                    placeholder={field.placeholder}
                    onChange={(e) => set(field.name, e.target.value)}
                  />
                ) : (
                  <input
                    value={values[field.name] ?? ""}
                    placeholder={field.placeholder}
                    autoFocus={field === fields[0]}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") submit();
                    }}
                    onChange={(e) => set(field.name, e.target.value)}
                  />
                )}
              </label>
            );
          })}
        </div>

        <footer className="schedule-foot">
          <button className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" onClick={submit}>
            <Check size={15} /> {confirmText}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

interface PromptRequest {
  title: string;
  subtitle?: string;
  label: string;
  value?: string;
  confirmText?: string;
  resolve: (value: string | null) => void;
}

/**
 * `const [prompt, promptUI] = usePrompt()` — 应用内版的 window.prompt。
 * 桌面端跑在 webview 里，系统原生输入框既不像本产品，也不受我们控制。
 * 取消返回 null，确定返回输入的字符串。
 */
export function usePrompt(): [
  (opts: Omit<PromptRequest, "resolve">) => Promise<string | null>,
  React.ReactNode,
] {
  const [request, setRequest] = useState<PromptRequest | null>(null);

  const prompt = useCallback(
    (opts: Omit<PromptRequest, "resolve">) =>
      new Promise<string | null>((resolve) => {
        setRequest({ ...opts, resolve });
      }),
    [],
  );

  const ui = request ? (
    <FormModal
      title={request.title}
      subtitle={request.subtitle}
      confirmText={request.confirmText}
      fields={[
        { name: "value", label: request.label, value: request.value ?? "" },
      ]}
      onConfirm={(values) => {
        setRequest(null);
        request.resolve(values.value ?? "");
      }}
      onClose={() => {
        setRequest(null);
        request.resolve(null);
      }}
    />
  ) : null;

  return [prompt, ui];
}
