// OS/browser notifications. Uses the standard web Notification API, which works
// both in the browser (web UI) and in the Tauri WebView2 desktop window — so one
// path covers both. Best-effort: silently no-ops if unsupported or not granted.

let asked = false;

export function ensureNotifyPermission(): void {
  if (asked || typeof Notification === "undefined") return;
  asked = true;
  if (Notification.permission === "default") {
    void Notification.requestPermission().catch(() => {});
  }
}

export function osNotify(title: string, body: string): void {
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  try {
    // tag: same-kind notifications replace instead of stacking.
    new Notification(title, { body, tag: "matrix" });
  } catch {
    /* some webviews throw on construct; ignore */
  }
}
