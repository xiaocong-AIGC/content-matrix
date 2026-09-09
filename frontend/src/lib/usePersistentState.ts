import { useEffect, useState } from "react";

/**
 * useState whose value survives component unmount within the session — so a
 * filter/page you set on 总览/内容库/… is still there when you navigate away and
 * come back (each list view is conditionally rendered, so plain useState resets).
 * Backed by sessionStorage (per browser tab/session, cleared on close), keyed by
 * a stable string. Falls back to in-memory if sessionStorage is unavailable.
 */
export function usePersistentState<T>(key: string, initial: T) {
  const storageKey = `ui:${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = sessionStorage.getItem(storageKey);
      return raw != null ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      /* storage full / disabled — keep in-memory only */
    }
  }, [storageKey, value]);
  return [value, setValue] as const;
}
