// Single source of truth for platform labels, so every page reads the same.
export const PLATFORM_LABEL: Record<string, string> = {
  douyin: "抖音",
  xhs: "小红书",
  both: "双平台",
};

export function platformLabel(p?: string | null): string {
  if (!p) return "抖音";
  return PLATFORM_LABEL[p] ?? p;
}

// "抖音号" / "小红书号" for the account-id line.
export function accountIdLabel(p?: string | null): string {
  return `${platformLabel(p)}号`;
}
