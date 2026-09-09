import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, X } from "lucide-react";

interface Request {
  title: string;
  detail?: string;
  confirmText?: string;
  danger?: boolean;
  resolve: (ok: boolean) => void;
}

/**
 * Styled in-app replacement for window.confirm — the desktop app runs in a
 * webview, where the native dialog is a foreign-looking OS box (and on some
 * builds never shows at all). Same shell as FormModal so 提示 look like one app.
 */
export function ConfirmModal({ request }: { request: Request }) {
  const { title, detail, confirmText = "确定", danger, resolve } = request;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") resolve(false);
      if (event.key === "Enter") resolve(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [resolve]);

  return createPortal(
    <div className="modal-backdrop" role="presentation" onMouseDown={() => resolve(false)}>
      <section
        className="schedule-modal confirm-modal"
        role="alertdialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="schedule-head">
          <div>
            <strong>{title}</strong>
            {detail ? <small>{detail}</small> : null}
          </div>
          <button className="ghost-icon" aria-label="关闭" onClick={() => resolve(false)}>
            <X size={18} />
          </button>
        </header>
        <footer className="schedule-foot">
          <button className="button secondary" onClick={() => resolve(false)}>
            取消
          </button>
          <button
            className={danger ? "button danger" : "button primary"}
            autoFocus
            onClick={() => resolve(true)}
          >
            {danger ? <AlertTriangle size={15} /> : null} {confirmText}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

/**
 * `const [confirm, confirmUI] = useConfirm()` — call `await confirm({...})`
 * anywhere and render `{confirmUI}` once in the component.
 */
export function useConfirm(): [
  (opts: Omit<Request, "resolve">) => Promise<boolean>,
  React.ReactNode,
] {
  const [request, setRequest] = useState<Request | null>(null);

  const confirm = useCallback(
    (opts: Omit<Request, "resolve">) =>
      new Promise<boolean>((resolve) => {
        setRequest({
          ...opts,
          resolve: (ok) => {
            setRequest(null);
            resolve(ok);
          },
        });
      }),
    [],
  );

  return [confirm, request ? <ConfirmModal request={request} /> : null];
}
