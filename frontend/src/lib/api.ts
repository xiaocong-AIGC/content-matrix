import type {
  ChatGroup,
  ContentItem,
  ContentStats,
  Device,
  ImageAsset,
  BroadcastMessage,
  BroadcastPreview,
  BroadcastSchedule,
  SupplyRunResult,
  SupplyState,
  PublishTask,
  TaskCreate,
} from "./types";

export interface BroadcastCreate {
  target_device_id: number;
  text: string;
  image_id?: number | null;
  mention_all: boolean;
  groups: string[];
  scheduled_at?: string | null;
}

const TOKEN_KEY = "admin_token";
// HTTP header values must be Latin1 (ISO-8859-1). A token typed with a Chinese IME
// can pick up full-width chars (e.g. "－" U+FF0D instead of "-") — NFKC folds those
// back to ASCII. Anything still outside Latin1 is stripped so it can NEVER throw
// "String contains non ISO-8859-1 code point" and brick every request; a genuinely
// wrong token then just 401s gracefully.
const sanitizeToken = (t: string) =>
  t.trim().normalize("NFKC").replace(/[^\x00-\xFF]/g, "");
export const getAdminToken = () =>
  sanitizeToken(localStorage.getItem(TOKEN_KEY) ?? "");
export const setAdminToken = (t: string) =>
  localStorage.setItem(TOKEN_KEY, sanitizeToken(t));
export const clearAdminToken = () => localStorage.removeItem(TOKEN_KEY);

// API 源：浏览器/开发态留空 → 相对路径走 Vite 代理；打包的桌面版(Tauri，无代理)
// 用 VITE_API_ORIGIN 指向后端绝对地址(如 http://127.0.0.1:8010)。
const API_ORIGIN = (
  (import.meta.env.VITE_API_ORIGIN as string | undefined) ?? ""
).replace(/\/$/, "");
export const apiUrl = (path: string) => `${API_ORIGIN}${path}`;

/** 把后端的报错读成一句人话。
 *
 * FastAPI 校验失败时 `detail` 是**一个对象数组**（每项 loc/msg/type），
 * 直接塞进 Error 里会变成「[object Object],[object Object]」——
 * 界面上写着「保存失败」却什么也没说，等于没报错。 */
function readDetail(payload: unknown): string | undefined {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (!detail) return undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const lines = detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc)
          ? item.loc.filter((x) => x !== "body").join(".")
          : "";
        return [field, item.msg].filter(Boolean).join("：");
      })
      .filter(Boolean);
    if (lines.length) return lines.join("；");
  }
  return undefined;
}

// Append the admin token as a query param for <img>/file URLs (can't set headers).
export const fileUrl = (path: string) => {
  const full = apiUrl(path);
  const t = getAdminToken();
  if (!t) return full;
  return full + (full.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
};

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

export function authHeaders(extra?: HeadersInit): HeadersInit {
  return { "X-Admin-Token": getAdminToken(), ...extra };
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(`/api/v1${path}`), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": getAdminToken(),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    if (response.status === 401) onUnauthorized();
    const payload = await response.json().catch(() => null);
    throw new Error(readDetail(payload) ?? `请求失败 (${response.status})`);
  }
  // 删除类接口返回 204 No Content（空 body），直接 response.json() 会抛
  // "Unexpected end of JSON input" —— 调用方把它当成失败，于是界面提示「删除失败」，
  // 可东西其实已经删掉了，刷新一下就没了。所以空 body 要当成成功。
  if (response.status === 204 || response.status === 205) {
    return undefined as T;
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export interface Identity {
  name: string;
  role: string;
  status: string; // active | frozen | expired
  expires_at?: string | null;
  days_left?: number | null;
  renewal_requested?: boolean;
  auth: boolean;
}
export interface ConsoleToken {
  id: number;
  name: string;
  role: string;
  status: string; // active | frozen | expired
  frozen: boolean;
  expires_at: string | null;
  days_left: number | null;
  renewal_requested: boolean;
  last_used_at: string | null;
  created_at: string;
}
export const getMe = () => api<Identity>("/auth/me");
export const listConsoleTokens = () => api<ConsoleToken[]>("/auth/tokens");
export const createConsoleToken = (
  name: string,
  role: string,
  valid_days: number | null,
) =>
  api<ConsoleToken & { token: string }>("/auth/tokens", {
    method: "POST",
    body: JSON.stringify({ name, role, valid_days }),
  });
export const extendConsoleToken = (id: number, days: number) =>
  api<ConsoleToken>(`/auth/tokens/${id}/extend`, {
    method: "POST",
    body: JSON.stringify({ days }),
  });
export const freezeConsoleToken = (id: number, frozen: boolean) =>
  api<ConsoleToken>(`/auth/tokens/${id}/freeze`, {
    method: "PATCH",
    body: JSON.stringify({ frozen }),
  });
export const deleteConsoleToken = (id: number) =>
  fetch(apiUrl(`/api/v1/auth/tokens/${id}`), { method: "DELETE", headers: authHeaders() });

export interface RenewalInfo {
  note: string;
  wechat: boolean;
  alipay: boolean;
}
export const getRenewalInfo = () => api<RenewalInfo>("/auth/renewal-info");
export const renewalQrUrl = (kind: "wechat" | "alipay") =>
  fileUrl(`/api/v1/auth/renewal-qr/${kind}`);

export interface RenewalRequest {
  id: number;
  token_id: number;
  token_name: string;
  amount: string;
  paid_at_text: string;
  reference: string;
  has_screenshot: boolean;
  status: string; // pending | approved | rejected
  review_note: string;
  days_granted: number | null;
  created_at: string;
  reviewed_at: string | null;
}
export async function submitRenewal(fields: {
  amount: string;
  paid_at_text: string;
  reference: string;
  screenshot?: File;
}): Promise<RenewalRequest> {
  const form = new FormData();
  form.append("amount", fields.amount);
  form.append("paid_at_text", fields.paid_at_text);
  form.append("reference", fields.reference);
  if (fields.screenshot) form.append("screenshot", fields.screenshot);
  const res = await fetch(apiUrl("/api/v1/auth/request-renewal"), {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? "提交失败");
  return res.json();
}
export const getMyRenewal = () => api<RenewalRequest | null>("/auth/my-renewal");
export const listRenewalRequests = (status = "pending") =>
  api<RenewalRequest[]>(`/auth/renewal-requests?status=${status}`);
export const approveRenewal = (id: number, days: number, note = "") =>
  api<RenewalRequest>(`/auth/renewal-requests/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ days, note }),
  });
export const rejectRenewal = (id: number, note: string) =>
  api<RenewalRequest>(`/auth/renewal-requests/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
export const renewalScreenshotUrl = (id: number) =>
  fileUrl(`/api/v1/auth/renewal-requests/${id}/screenshot`);
export async function setRenewalConfig(
  note: string,
  wechat?: File,
  alipay?: File,
): Promise<{ ok: boolean }> {
  const form = new FormData();
  form.append("note", note);
  if (wechat) form.append("wechat_qr", wechat);
  if (alipay) form.append("alipay_qr", alipay);
  const res = await fetch(apiUrl("/api/v1/auth/renewal-config"), {
    method: "PUT",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? "保存失败");
  return res.json();
}

export const listTasks = () => api<PublishTask[]>("/tasks");
export const getTask = (id: number) => api<PublishTask>(`/tasks/${id}`);
export const listDevices = () => api<Device[]>("/devices");
export const createTask = (payload: TaskCreate) =>
  api<PublishTask>("/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export type TaskActionKind = "confirm_published" | "cancel" | "retry";

export const runTaskAction = (id: number, action: TaskActionKind) =>
  api<PublishTask>(`/tasks/${id}/actions`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });

export const listContent = (city?: string) =>
  api<ContentItem[]>(
    `/content${city ? `?city=${encodeURIComponent(city)}` : ""}`,
  );
export const contentStats = () => api<ContentStats>("/content/stats");
// 城市下拉选项（账号所在城市 + 内容库已用城市，含 通用/全国）。
export const listCities = () =>
  api<{ cities: string[] }>("/content/cities").then((r) => r.cities);
export interface PerformanceRow {
  content_id: number;
  platform: string;
  title: string;
  cover_title?: string;
  body?: string;
  topics?: string[];
  excerpt?: string;
  views: number;
  likes: number;
  collects: number;
  comments: number;
  engagement: number;
  captured_at: string;
  // 「谁发的、哪天发的」——榜上只有标题和数字时，看不出一条数字低是内容不行
  // 还是昨天刚发；也没法把爆款归到某个号头上。对不上发布任务的行是 null。
  device_name?: string | null;
  account_nickname?: string | null;
  city?: string | null;
  published_at?: string | null;
  age_days?: number | null;
}
export const contentPerformance = () =>
  api<PerformanceRow[]>("/content/performance");

// Ask every device to re-run 回采 (manual "更新数据" on 效果榜).
export const refreshMetrics = () =>
  api<{ status: string; devices: number }>("/devices/refresh-metrics", {
    method: "POST",
  });

// 设备卡片「更新账号」— 强制该设备下次心跳重读本机登录的账号（不碰发布流程）。
export const refreshAccount = (deviceId: number) =>
  api<{ status: string }>(`/devices/${deviceId}/refresh-account`, {
    method: "POST",
  });

export interface PublishNotification {
  id: number;
  task_id: number;
  status: string;
  ok: boolean;
  platform: string;
  account: string;
  device_name: string | null;
  title: string;
  kind: string; // text | group_message
  is_fanout: boolean;
  error: string | null;
  finished_at: string | null;
}
// 发布结果通知中心 — recent publish results (success/fail), newest first.
export const listNotifications = () =>
  api<PublishNotification[]>("/notifications?limit=40");

// 运维告警中心 — 当前活跃异常（设备掉线 / 账号限流 / 近期发布失败）。
export interface OpsAlert {
  id: string;
  severity: "critical" | "warning" | "info";
  kind: string;
  title: string;
  detail: string;
  target: string | null;
  device_id: number | null;
  at: string | null;
}
export interface AlertsResult {
  alerts: OpsAlert[];
  counts: { critical: number; warning: number; info: number };
  total: number;
}
export const listAlerts = () => api<AlertsResult>("/alerts");

// 检测设备: scan adb once, auto-install the agent on any new phone, sync accounts.
export const detectDevices = () =>
  api<{
    ok: boolean;
    connected: number;
    newly_provisioned: number;
    output: string;
  }>("/devices/detect", { method: "POST" });

// 删除设备（连同其绑定的所有平台账号）。后端返回 204。
export const deleteDevice = async (id: number): Promise<void> => {
  const r = await fetch(apiUrl(`/api/v1/devices/${id}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!r.ok && r.status !== 204) {
    throw new Error((await r.json().catch(() => null))?.detail ?? "删除失败");
  }
};

// 只删除该设备的某个平台账号（如仅删小红书号），其他账号/设备保留。
// 若是该设备最后一个账号，后端会顺带移除设备（device_removed=true）。
export const deleteAccount = (deviceId: number, platform: string) =>
  api<{ status: string; device_removed: boolean }>(
    `/devices/${deviceId}/accounts/${platform}`,
    { method: "DELETE" },
  );

export interface ContentDraft {
  id: number;
  cover_title: string;
  title: string;
  body: string;
  topics: string[];
  platform: string;
  city: string;
  source_content_id: number | null;
  source_note: string;
  model: string;
  status: string;
  accepted_content_id: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cny: number;
  dup_score: number;
  dup_of: string;
  created_at: string;
}
export interface DraftStats {
  total_drafts: number;
  by_status: Record<string, number>;
  duplicate: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cny: number;
}
export const draftStats = () => api<DraftStats>("/content/drafts/stats");
export const engineStatus = () =>
  api<{ configured: boolean }>("/content/engine/status");
export interface AiConfig {
  configured: boolean;
  masked: string;
  source: "custom" | "env" | "none";
  base_url: string;
  model: string;
}
// Admin-only: operators get 403 (caller should treat as "hidden").
export const getAiConfig = () => api<AiConfig>("/content/engine/aiconfig");
export const saveAiConfig = (cfg: {
  api_key?: string;
  base_url?: string;
  model?: string;
}) =>
  api<AiConfig>("/content/engine/aiconfig", {
    method: "PUT",
    body: JSON.stringify(cfg),
  });
export const getEnginePrompt = () =>
  api<{ prompt: string; default: string; is_custom: boolean }>(
    "/content/engine/prompt",
  );
export const setEnginePrompt = (prompt: string) =>
  api<{ prompt: string; default: string; is_custom: boolean }>(
    "/content/engine/prompt",
    { method: "PUT", body: JSON.stringify({ prompt }) },
  );
export const previewEnginePrompt = (
  platform: string,
  theme?: string,
  presetId?: number | null,
) =>
  api<{ system: string; user: string; exemplar_count: number }>(
    `/content/engine/preview?platform=${encodeURIComponent(platform)}${
      theme ? `&theme=${encodeURIComponent(theme)}` : ""
    }${presetId ? `&preset_id=${presetId}` : ""}`,
  );
export const generateDrafts = (payload: {
  count: number;
  platform: string;
  theme?: string;
  source_content_ids?: number[];
  preset_id?: number | null;
}) =>
  api<ContentDraft[]>("/content/generate", {
    method: "POST",
    body: JSON.stringify(payload),
  });

// —— AI 生成预设（多套提示词/样本，运营可各自保存、生成时任选一套）——
export interface PromptPreset {
  id: number;
  name: string;
  owner: string;
  system_prompt: string;
  sample_mode: string; // default | custom | pick
  sample_text: string;
  sample_ids: number[];
  created_at: string;
  updated_at: string;
}
// —— 首次配置向导 ——
export interface BootstrapInfo {
  fresh: boolean;
  master_token: string;
  adb_endpoints: string;
}
export const getBootstrap = () => api<BootstrapInfo>("/auth/bootstrap");
export const finishBootstrap = () =>
  api<{ ok: boolean }>("/auth/bootstrap-done", { method: "POST" });
export const setAdbEndpoints = (endpoints: string) =>
  api<{ endpoints: string }>("/agent/adb-endpoints", {
    method: "PUT",
    body: JSON.stringify({ endpoints }),
  });

export const listPresets = () => api<PromptPreset[]>("/content/engine/presets");
export const createPreset = (p: {
  name: string;
  system_prompt: string;
  sample_mode: string;
  sample_text: string;
  sample_ids?: number[];
}) =>
  api<PromptPreset>("/content/engine/presets", {
    method: "POST",
    body: JSON.stringify({ sample_ids: [], ...p }),
  });
export const updatePreset = (id: number, p: {
  name: string;
  system_prompt: string;
  sample_mode: string;
  sample_text: string;
  sample_ids?: number[];
}) =>
  api<PromptPreset>(`/content/engine/presets/${id}`, {
    method: "PUT",
    body: JSON.stringify({ sample_ids: [], ...p }),
  });
export const deletePreset = async (id: number): Promise<void> => {
  const r = await fetch(apiUrl(`/api/v1/content/engine/presets/${id}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!r.ok && r.status !== 204) {
    throw new Error((await r.json().catch(() => null))?.detail ?? "删除失败");
  }
};
export const listDrafts = (status = "pending") =>
  api<ContentDraft[]>(`/content/drafts?status=${status}`);
export const acceptDraft = (id: number, city: string) =>
  api<ContentItem>(`/content/drafts/${id}/accept`, {
    method: "POST",
    body: JSON.stringify({ city }),
  });
export const rejectDraft = (id: number) =>
  api<ContentDraft>(`/content/drafts/${id}/reject`, { method: "POST" });
export const updateDraft = (
  id: number,
  payload: Partial<{
    cover_title: string;
    title: string;
    body: string;
    topics: string[];
    platform: string;
  }>,
) =>
  api<ContentDraft>(`/content/drafts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
export const createContent = (payload: {
  cover_title: string;
  title: string;
  body: string;
  topics: string[];
  city: string;
  platform: string;
}) =>
  api<ContentItem>("/content", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const deleteContent = (id: number) =>
  fetch(apiUrl(`/api/v1/content/${id}`), { method: "DELETE", headers: authHeaders() });
// 「发布中」取消并退回待发布（同时取消占用它的任务；也修复卡在发布中的陈旧数据）。
export const returnContentToPending = (id: number) =>
  api<ContentItem & { cancelled_tasks: number }>(
    `/content/${id}/return-to-pending`,
    { method: "POST" },
  );
export async function importContentCsv(
  file: File,
): Promise<{ created: number; skipped: number }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(apiUrl("/api/v1/content/import"), {
    method: "POST",
    body: form,
    headers: authHeaders(),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? "导入失败");
  }
  return res.json();
}
export const setDeviceAutoPublish = (
  id: number,
  auto_publish: boolean,
  daily_quota?: number,
  platform = "douyin",
) =>
  api<Device>(`/devices/${id}/auto-publish`, {
    method: "PATCH",
    body: JSON.stringify({ auto_publish, daily_quota, platform }),
  });
export const setDeviceProfile = (
  id: number,
  payload: { city?: string; name?: string; platform?: string },
) =>
  api<Device>(`/devices/${id}/profile`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
export const schedulePosts = (
  id: number,
  times: string[],
  platform = "douyin",
  // Parallel to `times`: pin a content id to a slot, or null for 随机.
  contentIds?: (number | null)[],
) =>
  api<{
    scheduled: number;
    requested: number;
    skipped_quota: number;
    pool_exhausted: boolean;
    daily_quota: number;
  }>(`/devices/${id}/schedule`, {
    method: "POST",
    body: JSON.stringify({
      times,
      platform,
      ...(contentIds ? { content_ids: contentIds } : {}),
    }),
  });
export interface BatchScheduleResult {
  total_scheduled: number;
  accounts: number;
  pool_exhausted: boolean;
  results: {
    device_id: number;
    name: string;
    scheduled: number;
    skipped_quota: number;
    pool_exhausted: boolean;
    error?: string;
  }[];
}
// 跨账号批量排期: same slots to every account, content auto-assigned + de-duplicated.
export const scheduleBatch = (
  deviceIds: number[],
  times: string[],
  platform = "douyin",
) =>
  api<BatchScheduleResult>("/devices/schedule-batch", {
    method: "POST",
    body: JSON.stringify({ device_ids: deviceIds, times, platform }),
  });

export const listGroups = (deviceId: number) =>
  api<ChatGroup[]>(`/devices/${deviceId}/groups`);
export const createBroadcast = (payload: BroadcastCreate) =>
  api<PublishTask>("/broadcasts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const listImages = (category?: string) =>
  api<ImageAsset[]>(
    `/images${category ? `?category=${encodeURIComponent(category)}` : ""}`,
  );
export const listImageCategories = () => api<string[]>("/images/categories");
export const deleteImage = (id: number) =>
  api<void>(`/images/${id}`, { method: "DELETE" });
export const updateImage = (
  id: number,
  payload: { title?: string; category?: string },
) =>
  api<ImageAsset>(`/images/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
export async function uploadImage(
  file: File,
  meta?: { title?: string; category?: string },
): Promise<ImageAsset> {
  const form = new FormData();
  form.append("file", file);
  if (meta?.title) form.append("title", meta.title);
  if (meta?.category) form.append("category", meta.category);
  const res = await fetch(apiUrl("/api/v1/images"), {
    method: "POST",
    body: form,
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("图片上传失败");
  return (await res.json()) as ImageAsset;
}

export const setDeviceHealth = (
  id: number,
  health: string,
  health_message?: string | null,
  platform = "douyin",
) =>
  api<Device>(`/devices/${id}/health`, {
    method: "PATCH",
    body: JSON.stringify({ health, health_message, platform }),
  });

/** 「在跑的号」的唯一定义，由后端给（和企微群通知同一份口径）。
 *  以前首页自己算一套、通知算另一套，群里说 14 个号、首页说 /44，运营不知道信谁。 */
export async function getAccountRoster(): Promise<{
  in_service_ids: number[];
  in_service: number;
  daily_target: number;
  today_target: number;
}> {
  return api("/devices/roster");
}

// ---------------------------------------------------------------------------
// 群推送消息库 —— 可反复用的消息，自动推送就是从这里挑
// ---------------------------------------------------------------------------
export interface BroadcastMessageIn {
  text: string;
  image_id?: number | null;
  mention_all: boolean;
  city: string;
  enabled: boolean;
}

export const listBroadcastMessages = () =>
  api<BroadcastMessage[]>("/broadcast-library");
export const createBroadcastMessage = (payload: BroadcastMessageIn) =>
  api<BroadcastMessage>("/broadcast-library", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const updateBroadcastMessage = (
  id: number,
  payload: BroadcastMessageIn,
) =>
  api<BroadcastMessage>(`/broadcast-library/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteBroadcastMessage = (id: number) =>
  api<void>(`/broadcast-library/${id}`, { method: "DELETE" });

export const getBroadcastSchedule = () =>
  api<BroadcastSchedule>("/broadcast-library/schedule");
export const saveBroadcastSchedule = (payload: {
  daily_count: number;
  windows: string;
  mode: "same" | "rotate";
}) =>
  api<BroadcastSchedule>("/broadcast-library/schedule", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
/** 立刻停：次数置 0 **并且**取消今天还排着没执行的那些。
 *  只置 0 的话运营点了停止、群里还在冒消息。 */
/** 立刻停：`in_flight` 是已经被手机领走、正在发的那几条 —— 停不下来。 */
export const stopBroadcastSchedule = () =>
  api<{ cancelled: number; in_flight: number } & BroadcastSchedule>(
    "/broadcast-library/schedule/stop",
    { method: "POST" },
  );
/** 今天每个账号几点推、推哪一条。时刻和挑法都可复现，所以看到的就是会发生的。
 *  传 override 就是**试算**：按一套还没保存的设置算，走的是同一套算法。 */
export const previewBroadcast = (override?: {
  daily_count: number;
  windows: string;
  mode: string;
}) =>
  override
    ? api<BroadcastPreview>("/broadcast-library/schedule/preview", {
        method: "POST",
        body: JSON.stringify(override),
      })
    : api<BroadcastPreview>("/broadcast-library/schedule/preview");

/** 全局「每个账号每天几篇」。⚠ 引擎实际用的是 min(这个值, 该号 daily_quota)，
 *  首页和企微群也是同一条规则 —— 别在别处再算一份。 */
export const getPublishTarget = () =>
  api<{ daily_target: number; default: number }>("/content/publish-target");
export const setPublishTarget = (value: number) =>
  api<{ daily_target: number }>(`/content/publish-target?value=${value}`, {
    method: "PUT",
  });

/** 自动发布的时间段（publish:windows）。空 = 不限时段，手机空下来就发。 */
export const getPublishWindows = () =>
  api<{ windows: string; windows_readable: string; unrestricted: boolean }>(
    "/publish/windows",
  );
export const savePublishWindows = (windows: string) =>
  api<{ windows: string; windows_readable: string; unrestricted: boolean }>(
    "/publish/windows",
    { method: "PUT", body: JSON.stringify({ windows }) },
  );

export const setDeviceAutoBroadcast = (
  id: number,
  auto_broadcast: boolean,
  platform = "douyin",
) =>
  api<Device>(`/devices/${id}/auto-broadcast`, {
    method: "PATCH",
    body: JSON.stringify({ auto_broadcast, platform }),
  });
/** 每台设备读到了几个群，一次拿全（逐台问要 37 次请求）。 */
export const getGroupCounts = () =>
  api<Record<string, number>>("/devices/group-counts");

// ---------------------------------------------------------------------------
// 内容自动补货
// ---------------------------------------------------------------------------
export interface SupplySettings {
  enabled: boolean;
  enabled_cities: string[];
  min_days: number;
  batch_cap: number;
  quality_floor: number;
  city_tokens_raw: string;
}

export const getSupply = () => api<SupplyState>("/supply");
export const saveSupply = (payload: SupplySettings) =>
  api<SupplyState>("/supply", { method: "PUT", body: JSON.stringify(payload) });
/** 只保存各城市的地名，不碰生成参数。
 *  `saveSupply` 送的是整个设置对象 —— 用它保存词表会把四个参数一并写回，
 *  反过来改参数也会把整张词表重写一遍。而词表是地名检查的全部依据。 */
export const saveCityTokens = (city_tokens_raw: string) =>
  api<SupplyState>("/supply/city-tokens", {
    method: "PUT",
    body: JSON.stringify({ city_tokens_raw }),
  });
/** ⚠ 会真的调模型生成、真的花钱。只在运营明确点击时调。 */
export const runSupply = () =>
  api<{ result: SupplyRunResult } & SupplyState>("/supply/run", {
    method: "POST",
  });
