export type TaskStatus =
  | "queued"
  | "leased"
  | "running"
  | "waiting_confirmation"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface DeviceAccount {
  id: number;
  device_id: number;
  platform: string; // douyin | xhs
  nickname?: string | null;
  account_id?: string | null;
  city: string;
  auto_publish: boolean;
  /** 这个账号参不参与自动群推送。和 auto_publish 是两个独立开关 */
  auto_broadcast: boolean;
  daily_quota: number;
  health: string;
  health_message?: string | null;
  logged_in?: boolean | null; // true=已登录 false=未登录 null=未知
  today_published?: number;
}

export interface Device {
  id: number;
  device_code: string;
  name: string;
  platform: string;
  agent_version: string;
  android_version?: string;
  douyin_version?: string;
  douyin_nickname?: string | null;
  douyin_id?: string | null;
  city?: string;
  auto_publish?: boolean;
  daily_quota?: number;
  today_published?: number;
  health?: string;
  health_message?: string | null;
  accounts?: DeviceAccount[];
  capabilities?: string[];
  status: string;
  accessibility_ok?: boolean | null; // 无障碍是否绑定（就绪判断）
  current_task_id?: number;
  last_heartbeat_at: string;
}

export type ContentStatus = "pending" | "publishing" | "published" | "disabled";

export interface ContentItem {
  id: number;
  cover_title: string;
  title: string;
  body: string;
  topics: string[];
  city: string;
  platform: string; // douyin | xhs | both
  status: ContentStatus;
  published_device_id?: number | null;
  published_device_name?: string | null;
  published_device_nickname?: string | null;
  published_at?: string | null;
  created_at: string;
}

export interface ContentStats {
  pending: number;
  publishing: number;
  published: number;
  disabled: number;
  total: number;
}

export interface ImageAsset {
  id: number;
  filename: string;
  title: string;
  category: string;
  url: string;
  created_at: string;
}

export interface ChatGroup {
  id: number;
  device_id: number;
  group_name: string;
  member_count?: number | null;
  can_mention_all: boolean;
}

export interface ExecutionLog {
  id: number;
  level: string;
  step: string;
  message: string;
  context: Record<string, unknown>;
  created_at: string;
}

export interface Screenshot {
  id: number;
  step: string;
  created_at: string;
}

export interface PublishTask {
  id: number;
  name: string;
  platform: string; // douyin | xhs
  publish_type: string;
  cover_title: string;
  publish_title: string;
  body: string;
  topics: string[];
  media: string[];
  publish_mode: "manual_confirm" | "auto_publish";
  priority: number;
  target_device_id?: number;
  status: TaskStatus;
  current_step: string;
  progress: number;
  error_message?: string;
  device?: Device;
  account_nickname?: string | null; // 平台对应的发布账号昵称
  content_id?: number | null;
  target_groups?: string[];
  mention_all?: boolean;
  scheduled_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
  logs?: ExecutionLog[];
  screenshots?: Screenshot[];
}

export interface TaskCreate {
  name: string;
  platform?: string;
  cover_title: string;
  publish_title: string;
  body: string;
  topics: string[];
  publish_mode: "manual_confirm" | "auto_publish";
  priority: number;
  target_device_id?: number;
  scheduled_at?: string | null;
}


/** 群推送消息库里的一条。和作品池最大的区别：**可以反复用**，发出去不消失。 */
export interface BroadcastMessage {
  id: number;
  text: string;
  image_path?: string | null;
  /** 图库里的图片 id；库里只存文件路径，接口按路径反查出来给前端 */
  image_id?: number | null;
  /** 能直接放进 <img> 的地址（还要过一遍 fileUrl 带上令牌） */
  image_url?: string | null;
  mention_all: boolean;
  /** 「通用」/「全国」= 所有号都能用；填具体城市 = 只有该城市的号会用它 */
  city: string;
  enabled: boolean;
  sent_count: number;
  last_sent_at?: string | null;
  created_at: string;
}

export interface BroadcastSchedule {
  daily_count: number;
  /** ⚠ 不是「今天还有几条没推」。任务只在时刻到点后才创建，所以这个数
   *  正常是 0~3 —— 它是「已到点、还没被手机领走」的量，也就是点停止会取消掉的量。
   *  「今天还要推几条」看 upcoming_today。 */
  queued_now: number;
  upcoming_today: number;
  /** 消息库里启用的条数。0 而又开着自动推送 = 到点没东西可发 */
  usable_messages: number;
  windows: string;
  windows_readable: string;
  mode: "same" | "rotate";
  accounts_on: string[];
}

/** 某个城市还差多少篇才够 `min_days` 天的量。分母只算最近 48 小时发过的号。 */
export interface SupplyGap {
  city: string;
  accounts: number;
  have: number;
  need: number;
  short: number;
}

export interface SupplyState {
  /** 还有没有城市开着（= enabled_cities 非空）。真正说了算的是下面那个 */
  enabled: boolean;
  /** 开着自动生成的城市。按城市开，不是一个总开关一开全生成 */
  enabled_cities: string[];
  min_days: number;
  batch_cap: number;
  quality_floor: number;
  city_tokens_raw: string;
  city_tokens: Record<string, string[]>;
  gaps: SupplyGap[];
  short_total: number;
  last_run?: string | null;
}

export interface SupplyRunResult {
  skipped?: string;
  gaps?: string[];
  cities?: string[];
  made?: number;
  accepted?: number;
  rejected?: string[];
}

/** 状态由**后端**给。前端绝不能拿 HH:MM 和 new Date() 比 ——
 *  运营的机器时区不保证是 +8，远程桌面更不保证。 */
export type BroadcastSlotState =
  | "upcoming"   // 还没到点
  | "due"        // 到点了，两分钟一轮的排程马上会排
  | "missed"     // 过了补发窗口，今天不会推了
  | "queued"     // 已经排进队列，等手机来领
  | "sent"
  | "failed"
  | "cancelled";

/** 自动推送预览里的一格：某个账号在某个时刻会推的那一条。 */
export interface BroadcastPreviewSlot {
  time: string;
  state: BroadcastSlotState;
  /** true = 队列里有这条真实任务，但它的时刻不在当前计划里
   *  （改过时间段之后的旧任务，它照发） */
  orphan: boolean;
  /** false = 只是「预计」。rotate 模式挑的是推得最少的那条，会随当天推送情况变 */
  certain: boolean;
  text: string;
  has_image: boolean;
  image_url?: string | null;
  mention_all: boolean;
  message_id?: number | null;
  message_city?: string | null;
  task_id?: number | null;
  error?: string | null;
}

export interface BroadcastPreviewRow {
  account: string;
  account_id: number;
  device_id: number;
  device?: string | null;
  city?: string | null;
  health: string;
  logged_in?: boolean | null;
  /** 非空 = 这个账号今天一条都不会推，这里是原因 */
  blocked?: string | null;
  /** 这台设备读到了几个群。0 = 打开了也推不出去 */
  groups: number;
  /** 这个城市能用的消息条数（本城 + 通用）。0 = 到点没东西可发 */
  usable_messages: number;
  times: BroadcastPreviewSlot[];
}

export interface BroadcastPreview {
  /** true = 这是按一套还没保存的设置试算的 */
  dry_run: boolean;
  today: string;
  daily_count: number;
  windows: string;
  windows_readable: string;
  mode: "same" | "rotate";
  rows: BroadcastPreviewRow[];
  summary: {
    accounts: number;
    blocked_accounts: number;
    planned: number;
    upcoming: number;
    due: number;
    queued: number;
    sent: number;
    failed: number;
    cancelled: number;
    missed: number;
  };
}
