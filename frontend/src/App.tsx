import { useCallback, useEffect, useRef, useState } from "react";
import {
  Archive,
  KeyRound,
  ArrowUpRight,
  BarChart3,
  Command,
  LayoutDashboard,
  Library,
  Sparkles,
  PackagePlus,
  Megaphone,
  MessageSquareText,
  CalendarClock,
  Rocket,
  LoaderCircle,
  Plus,
  Radio,
  RefreshCw,
  Smartphone,
  X,
  Wifi,
} from "lucide-react";

import { BroadcastView } from "./components/BroadcastView";
import { BroadcastLibraryView } from "./components/BroadcastLibraryView";
import { AutoBroadcastView } from "./components/AutoBroadcastView";
import { ContentLibrary } from "./components/ContentLibrary";
import { CreateTaskModal } from "./components/CreateTaskModal";
import { DeviceView } from "./components/DeviceView";
import { DeviceTasksView } from "./components/DeviceTasksView";
import { FormModal } from "./components/FormModal";
import { HistoryView } from "./components/HistoryView";
import { PerformanceView } from "./components/PerformanceView";
import { AIStudioView } from "./components/AIStudioView";
import { SupplyView } from "./components/SupplyView";
import { AutoPublishView } from "./components/AutoPublishView";
import { TokenManagementView } from "./components/TokenManagementView";
import { NotificationBell } from "./components/NotificationBell";
import { AlertBell } from "./components/AlertBell";
import { Overview } from "./components/Overview";
import { SchedulePublishModal } from "./components/SchedulePublishModal";
import { BatchScheduleModal } from "./components/BatchScheduleModal";
import { FirstRunWizard } from "./components/FirstRunWizard";
import {
  clearAdminToken,
  contentStats,
  createTask,
  deleteDevice,
  deleteAccount,
  getAdminToken,
  getBootstrap,
  getTask,
  listDevices,
  listTasks,
  runTaskAction,
  setAdminToken,
  setDeviceHealth,
  setDeviceProfile,
  setUnauthorizedHandler,
  type TaskActionKind,
} from "./lib/api";
import type { Device, DeviceAccount, PublishTask, TaskCreate } from "./lib/types";

type AccountRef = { device: Device; account: DeviceAccount };

type View =
  | "overview"
  | "content"
  | "supply"
  | "ai"
  | "publish-auto"
  | "broadcast-library"
  | "broadcast-auto"
  | "broadcast"
  | "devices"
  | "performance"
  | "history"
  | "tokens";
type Notice = { tone: "success" | "error"; title: string; message: string };

type NavItem = {
  id: View;
  label: string;
  note: string;
  icon: typeof LayoutDashboard;
};
type NavGroup = { section: string | null; items: NavItem[] };

// Two business lines kept visually separate in the nav: 内容发布 vs 群发.
const navGroups: NavGroup[] = [
  {
    section: null,
    items: [
      { id: "overview" as const, label: "账号总览", note: "ACCOUNTS", icon: LayoutDashboard },
    ],
  },
  {
    section: "内容发布",
    items: [
      { id: "content" as const, label: "内容库", note: "CONTENT", icon: Library },
      // 库 → 自动补库 → 手动来一发。和「群推送」那组同构。
      { id: "supply" as const, label: "自动生成", note: "AUTO CREATE", icon: PackagePlus },
      { id: "ai" as const, label: "AI 创作", note: "AI STUDIO", icon: Sparkles },
      // 前三项回答「内容从哪来」，这一项回答「怎么发出去」，是这条线的出口。
      // 和「群推送 → 自动推送」严格对称，学一次会两个。
      {
        id: "publish-auto" as const,
        label: "自动发布",
        note: "AUTO PUBLISH",
        icon: Rocket,
      },
    ],
  },
  {
    // 和「内容发布」同构：第一条是**库**，后面是拿库里的东西去发的入口。
    // 学会一边就等于学会另一边。
    section: "群推送",
    items: [
      {
        id: "broadcast-library" as const,
        label: "消息库",
        note: "MESSAGES",
        icon: MessageSquareText,
      },
      {
        id: "broadcast-auto" as const,
        label: "自动推送",
        note: "AUTO PUSH",
        icon: CalendarClock,
      },
      // 改名：这个页面做的就是"现在挑几个群发一发"，不是自动化。
      // 叫「群推送」会和自动推送混成一件事。
      { id: "broadcast" as const, label: "临时群发", note: "BROADCAST", icon: Megaphone },
    ],
  },
  {
    section: "账号与记录",
    items: [
      { id: "devices" as const, label: "设备列表", note: "DEVICES", icon: Smartphone },
      { id: "performance" as const, label: "效果榜", note: "ANALYTICS", icon: BarChart3 },
      { id: "history" as const, label: "执行记录", note: "HISTORY", icon: Archive },
      { id: "tokens" as const, label: "令牌管理", note: "TOKENS", icon: KeyRound },
    ],
  },
];
const navigation = navGroups.flatMap((g) => g.items);

export default function App() {
  const [tasks, setTasks] = useState<PublishTask[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedId, setSelectedId] = useState<number>();
  const [selectedTask, setSelectedTask] = useState<PublishTask>();
  const [view, setView] = useState<View>("overview");
  const [selectedDeviceId, setSelectedDeviceId] = useState<number>();
  // Which platform tab to open the device page on (set when an account card is
  // clicked, so a 小红书 card opens 小红书 — not the default first platform).
  const [selectedPlatform, setSelectedPlatform] = useState<string>();
  const [contentPending, setContentPending] = useState(0);
  const [showBatchSchedule, setShowBatchSchedule] = useState(false);
  // 记住进入设备详情前所在的页：从卡片进来的返回总览，从设备列表进来的返回列表。
  const [deviceOrigin, setDeviceOrigin] = useState<"overview" | "devices">(
    "devices",
  );
  // <main> is the scroll container (height:100vh; overflow:auto). React keeps its
  // scrollTop across view switches → clicking a card while the dashboard is
  // scrolled down carried that scroll into the device page. Reset on nav change,
  // EXCEPT when returning to 总览 from a card (restore the saved scroll so you
  // land back on the card you clicked, not the top).
  const mainRef = useRef<HTMLElement>(null);
  const overviewScroll = useRef(0);
  const restoreScroll = useRef<number | null>(null);
  useEffect(() => {
    if (restoreScroll.current != null) {
      const y = restoreScroll.current;
      restoreScroll.current = null;
      requestAnimationFrame(() => mainRef.current?.scrollTo({ top: y }));
    } else {
      mainRef.current?.scrollTo({ top: 0 });
    }
  }, [view, selectedDeviceId, selectedPlatform]);
  const [schedulingRef, setSchedulingRef] = useState<AccountRef>();
  const [cityRef, setCityRef] = useState<AccountRef>();
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<Notice>();
  const [authed, setAuthed] = useState(() => !!getAdminToken());
  const [tokenInput, setTokenInput] = useState("");
  const [wizard, setWizard] = useState<{ token: string; adb: string } | null>(null);
  const selectedIdRef = useRef<number>();

  // Fresh/换电脑 install (empty system, on THIS machine) → run the first-run wizard.
  useEffect(() => {
    if (authed) return;
    void getBootstrap()
      .then((b) => {
        if (b.fresh && b.master_token) {
          setWizard({ token: b.master_token, adb: b.adb_endpoints });
        }
      })
      .catch(() => {});
  }, [authed]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearAdminToken();
      setAuthed(false);
    });
  }, []);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  // 吸顶偏移量按**实测**算，不写死。
  //
  // `--stick-head-top` 原来是 `--topbar-h + --hero-h` = 94 + 124 = 218px，
  // 而 hero 早就改成了会长高的 min-height（宽屏下标题用 clamp，实测 162px）。
  // 结果是任何按这个数吸顶的表头都会被 hero 压住一截 —— 数字对不上现实。
  //
  // 每页的 hero 高度不一样、断点之间也不一样，所以只能量。ResizeObserver
  // 覆盖三种变化：换页面、改窗口宽度、hero 自己的内容变多（比如多一条警示）。
  useEffect(() => {
    const root = document.documentElement;
    const measure = () => {
      const topbar = document.querySelector(".topbar");
      const hero = document.querySelector(".catalog-hero");
      const top = topbar ? topbar.getBoundingClientRect().height : 0;
      // 只有**吸顶的** hero 才占位置；窄屏下它是 static，不参与偏移
      const heroH =
        hero && getComputedStyle(hero).position === "sticky"
          ? hero.getBoundingClientRect().height
          : 0;
      root.style.setProperty("--stick-head-top", `${Math.round(top + heroH)}px`);
    };
    measure();
    const ro = new ResizeObserver(measure);
    const topbar = document.querySelector(".topbar");
    const hero = document.querySelector(".catalog-hero");
    if (topbar) ro.observe(topbar);
    if (hero) ro.observe(hero);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [view]);

  const refresh = useCallback(async () => {
    try {
      const [taskItems, deviceItems, stats] = await Promise.all([
        listTasks(),
        listDevices(),
        contentStats(),
      ]);
      setTasks(taskItems);
      setDevices(deviceItems);
      setContentPending(stats.pending);
      setError("");
      const currentId = selectedIdRef.current;
      if (currentId && taskItems.some((task) => task.id === currentId)) {
        setSelectedTask(await getTask(currentId));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "服务没有响应");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh, authed]);

  async function selectTask(id: number) {
    setSelectedId(id);
    setSelectedTask(await getTask(id));
  }

  async function submitTask(payload: TaskCreate) {
    try {
      const task = await createTask(payload);
      await refresh();
      setNotice({
        tone: "success",
        title: "任务已发出",
        message: `任务 #${String(task.id).padStart(4, "0")} 已发给所选设备。`,
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "创建任务失败";
      setNotice({ tone: "error", title: "任务创建失败", message });
      throw reason;
    }
  }

  async function onScheduled(message: string) {
    await refresh();
    setNotice({ tone: "success", title: "已安排定时发布", message });
  }

  async function saveCity(ref: AccountRef, city: string) {
    try {
      await setDeviceProfile(ref.device.id, {
        city,
        platform: ref.account.platform,
      });
      await refresh();
      setNotice({
        tone: "success",
        title: "已更新城市分组",
        message: `${ref.account.nickname || ref.device.name} → ${city || "未分组"}`,
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "操作失败";
      setNotice({ tone: "error", title: "操作未完成", message });
    }
  }

  async function resolveHealth(ref: AccountRef) {
    try {
      await setDeviceHealth(ref.device.id, "normal", null, ref.account.platform);
      await refresh();
      setNotice({
        tone: "success",
        title: "账号已恢复正常",
        message: `${ref.account.nickname || ref.device.name} 已恢复，可重新安排发布。`,
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "操作失败";
      setNotice({ tone: "error", title: "操作未完成", message });
    }
  }

  async function taskAction(id: number, action: TaskActionKind) {
    try {
      const result = await runTaskAction(id, action);
      await refresh();
      const titles: Record<TaskActionKind, string> = {
        confirm_published: "已确认发布完成",
        cancel: "任务已取消",
        retry: "已重新发出",
      };
      const messages: Record<TaskActionKind, string> = {
        confirm_published: `任务 #${String(id).padStart(4, "0")} 已标记为发布完成。`,
        cancel: `任务 #${String(id).padStart(4, "0")} 已停止继续执行。`,
        retry: `已生成新任务 #${String(result.id).padStart(4, "0")} 重新发布。`,
      };
      setNotice({ tone: "success", title: titles[action], message: messages[action] });
      if (action === "retry") void selectTask(result.id);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "任务操作失败";
      setNotice({ tone: "error", title: "操作未完成", message });
      throw reason;
    }
  }

  function changeView(next: View) {
    setSelectedDeviceId(undefined);
    setView(next);
  }

  const onlineDevices = devices.filter(
    (device) => device.status === "online" || device.status === "busy",
  ).length;
  const activeTasks = tasks.filter(
    (task) => task.status === "running" || task.status === "leased",
  ).length;

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(undefined), 4200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function submitToken() {
    if (!tokenInput.trim()) return;
    setAdminToken(tokenInput);
    setTokenInput("");
    setError("");
    setAuthed(true);
  }

  if (!authed) {
    if (wizard) {
      return (
        <FirstRunWizard
          masterToken={wizard.token}
          adbDefault={wizard.adb}
          onDone={() => {
            setWizard(null);
            setAuthed(true);
          }}
        />
      );
    }
    return (
      <div className="login-shell">
        <form
          className="login-card"
          onSubmit={(e) => {
            e.preventDefault();
            submitToken();
          }}
        >
          <strong>发布室 · 控制台登录</strong>
          <p>请输入管理令牌。</p>
          <input
            type="password"
            value={tokenInput}
            autoFocus
            placeholder="管理令牌"
            onChange={(e) => setTokenInput(e.target.value)}
          />
          <button className="button primary" type="submit">
            进入控制台
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <aside className="sidebar">
        <div className="brand">
          <div className="brand-signal">
            <span />
            <span />
          </div>
          <div>
            <strong>发 布 室</strong>
            <span>CONTENT MATRIX PLATFORM</span>
          </div>
        </div>

        <div className="sidebar-credit">MADE BY CLAUDE &amp; HAO HUI</div>

        <nav aria-label="主导航">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.section ?? "_top"}>
              {group.section ? (
                <span className="nav-section">{group.section}</span>
              ) : null}
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    className={view === item.id ? "active" : ""}
                    onClick={() => changeView(item.id)}
                  >
                    <Icon size={17} strokeWidth={1.7} />
                    <span className="nav-copy">
                      <strong>{item.label}</strong>
                      <small>{item.note}</small>
                    </span>
                    <ArrowUpRight size={14} className="nav-arrow" />
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="agent-card">
          <div className="agent-card-head">
            <span className={`pulse ${error ? "error" : ""}`} />
            <small>AGENT NETWORK</small>
          </div>
          <strong>{error ? "服务离线" : "服务运行正常"}</strong>
          <p>实时同步各设备的任务状态与发布结果截图。</p>
          <div className="agent-card-data">
            <span>
              <b>{String(onlineDevices).padStart(2, "0")}</b>
              在线设备
            </span>
            <span>
              <b>{String(activeTasks).padStart(2, "0")}</b>
              执行中任务
            </span>
          </div>
        </div>
      </aside>

      <main ref={mainRef}>
        <header className="topbar">
          <div className="topbar-title">
            <span className="eyebrow">
              <Command size={12} />
              图文矩阵营销平台
            </span>
            <h1>
              {view === "overview" && "账号总览"}
              {view === "content" && "内容库"}
              {view === "supply" && "自动生成"}
              {view === "ai" && "AI 创作"}
              {view === "publish-auto" && "自动发布"}
              {view === "broadcast-library" && "消息库"}
              {view === "broadcast-auto" && "自动推送"}
              {view === "broadcast" && "临时群发"}
              {view === "devices" && "设备列表"}
              {view === "performance" && "效果榜"}
              {view === "history" && "执行记录"}
              {view === "tokens" && "令牌管理"}
            </h1>
          </div>

          <div className="topbar-actions">
            <div className="live-readout">
              <Radio size={14} />
              <span>LIVE</span>
              <b>{onlineDevices} DEVICE</b>
            </div>
            <AlertBell />
            <NotificationBell />
            <button
              className="icon-button"
              aria-label="刷新"
              title="刷新数据"
              disabled={loading}
              onClick={async () => {
                setLoading(true);
                await refresh();
                setNotice({
                  tone: "success",
                  title: "已刷新",
                  message: "设备和任务数据已更新",
                });
              }}
            >
              {loading ? (
                <LoaderCircle size={17} className="spinning" />
              ) : (
                <RefreshCw size={17} />
              )}
            </button>
            <button className="button primary" onClick={() => setCreating(true)}>
              <Plus size={16} />
              新建发布
            </button>
          </div>
        </header>

        {error ? (
          <div className="error-banner">
            <Wifi size={15} />
            <span>连不上服务器：{error}</span>
            <button onClick={() => void refresh()}>重新连接</button>
          </div>
        ) : null}

        {loading && !tasks.length && !error ? (
          <div className="boot-state">
            <div className="boot-mark">
              <span />
              <span />
            </div>
            <div>
              <small>CONNECTING TO AGENT NETWORK</small>
              <strong>正在载入工作区</strong>
            </div>
          </div>
        ) : null}

        {!loading && view === "overview" ? (
          <Overview
            devices={devices}
            contentPending={contentPending}
            onSchedule={(device, account) => setSchedulingRef({ device, account })}
            onResolveHealth={(device, account) =>
              void resolveHealth({ device, account })
            }
            onSetCity={(device, account) => setCityRef({ device, account })}
            onDeleteAccount={async (device, account) => {
              const label = account.platform === "xhs" ? "小红书" : "抖音";
              try {
                const r = await deleteAccount(device.id, account.platform);
                await refresh();
                setNotice({
                  tone: "success",
                  title: r.device_removed ? "已删除设备" : "已删除账号",
                  message: r.device_removed
                    ? `${device.name} 的最后一个账号已删，设备一并移除`
                    : `${device.name} 的${label}账号已移除，其他账号不受影响`,
                });
              } catch (reason) {
                setNotice({
                  tone: "error",
                  title: "删除失败",
                  message: reason instanceof Error ? reason.message : "请重试",
                });
              }
            }}
            onSelectDevice={(id, platform) => {
              // 记住来源=总览 + 当前滚动位置，返回时定位回你点的那张卡片。
              overviewScroll.current = mainRef.current?.scrollTop ?? 0;
              setDeviceOrigin("overview");
              setSelectedDeviceId(id);
              setSelectedPlatform(platform);
              setView("devices");
              // 自动打开该账号「最新一条内容」的状态（tasks 已按时间倒序）。
              const latest = tasks.find(
                (t) =>
                  (t.device?.id ?? t.target_device_id) === id &&
                  (!platform || t.platform === platform) &&
                  t.publish_type !== "group_message",
              );
              if (latest) void selectTask(latest.id);
              else setSelectedTask(undefined);
            }}
            onBatchSchedule={() => setShowBatchSchedule(true)}
          />
        ) : null}

        {!loading && view === "content" ? <ContentLibrary /> : null}

        {!loading && view === "supply" ? (
          <SupplyView
            devices={devices}
            onNotice={(tone, title, message) =>
              setNotice({ tone, title, message })
            }
          />
        ) : null}

        {!loading && view === "publish-auto" ? (
          <AutoPublishView
            devices={devices}
            onNotice={(tone, title, message) =>
              setNotice({ tone, title, message })
            }
            onGoto={setView}
          />
        ) : null}

        {!loading && view === "ai" ? <AIStudioView /> : null}

        {!loading && view === "broadcast-library" ? (
          <BroadcastLibraryView
            devices={devices}
            onNotice={(tone, title, message) =>
              setNotice({ tone, title, message })
            }
          />
        ) : null}

        {!loading && view === "broadcast-auto" ? (
          <AutoBroadcastView
            devices={devices}
            onNotice={(tone, title, message) =>
              setNotice({ tone, title, message })
            }
            onGoto={setView}
          />
        ) : null}

        {!loading && view === "broadcast" ? (
          <BroadcastView
            devices={devices}
            tasks={tasks}
            onNotice={(tone, title, message) =>
              setNotice({ tone, title, message })
            }
          />
        ) : null}

        {!loading && view === "devices" ? (
          (() => {
            const activeDevice = devices.find((d) => d.id === selectedDeviceId);
            return activeDevice ? (
              <DeviceTasksView
                device={activeDevice}
                tasks={tasks}
                initialPlatform={selectedPlatform}
                selectedTask={selectedTask}
                onSelectTask={(id) => void selectTask(id)}
                onAction={taskAction}
                backLabel={deviceOrigin === "overview" ? "返回总览" : "返回设备列表"}
                onBack={() => {
                  if (deviceOrigin === "overview") {
                    restoreScroll.current = overviewScroll.current;
                    setSelectedDeviceId(undefined);
                    setDeviceOrigin("devices");
                    setView("overview");
                  } else {
                    setSelectedDeviceId(undefined);
                  }
                }}
              />
            ) : (
              <DeviceView
                devices={devices}
                tasks={tasks}
                onSelectDevice={(id) => {
                  setDeviceOrigin("devices");
                  setSelectedDeviceId(id);
                  setSelectedPlatform(undefined);
                }}
                onDelete={async (device) => {
                  try {
                    await deleteDevice(device.id);
                    await refresh();
                    setNotice({
                      tone: "success",
                      title: "已删除设备",
                      message: `${device.name} 及其账号已移除`,
                    });
                  } catch (reason) {
                    setNotice({
                      tone: "error",
                      title: "删除失败",
                      message: reason instanceof Error ? reason.message : "请重试",
                    });
                  }
                }}
              />
            );
          })()
        ) : null}

        {!loading && view === "performance" ? <PerformanceView /> : null}

        {!loading && view === "tokens" ? <TokenManagementView /> : null}

        {!loading && view === "history" ? <HistoryView tasks={tasks} /> : null}
      </main>

      {creating ? (
        <CreateTaskModal
          devices={devices}
          onClose={() => setCreating(false)}
          onSubmit={submitTask}
        />
      ) : null}

      {schedulingRef ? (
        <SchedulePublishModal
          device={
            devices.find((d) => d.id === schedulingRef.device.id) ??
            schedulingRef.device
          }
          account={schedulingRef.account}
          contentPending={contentPending}
          onClose={() => setSchedulingRef(undefined)}
          onDone={(message) => void onScheduled(message)}
        />
      ) : null}

      {showBatchSchedule ? (
        <BatchScheduleModal
          devices={devices}
          contentPending={contentPending}
          onClose={() => setShowBatchSchedule(false)}
          onDone={(message) => void onScheduled(message)}
        />
      ) : null}

      {cityRef ? (
        <FormModal
          title="设置城市分组"
          subtitle={`${cityRef.account.nickname || cityRef.device.name} · ${
            cityRef.account.platform === "xhs" ? "小红书" : "抖音"
          }`}
          fields={[
            {
              name: "city",
              label: "城市",
              value: cityRef.account.city || "",
              placeholder: "如 上海、杭州",
              type: "select" as const,
              // 城市是开放集合：既要能选已有的，也要能直接打一个新的
              creatable: true,
              suggestions: [
                ...new Set(
                  devices
                    .flatMap((d) => d.accounts ?? [])
                    .map((a) => a.city || "")
                    .filter(Boolean),
                ),
              ],
            },
          ]}
          onConfirm={(values) => {
            const ref = cityRef;
            setCityRef(undefined);
            void saveCity(ref, values.city.trim());
          }}
          onClose={() => setCityRef(undefined)}
        />
      ) : null}

      {notice ? (
        <aside className={`notice notice-${notice.tone}`} role="status">
          <span className="notice-index">
            {notice.tone === "success" ? "OK" : "!"}
          </span>
          <div>
            <strong>{notice.title}</strong>
            <p>{notice.message}</p>
          </div>
          <button aria-label="关闭通知" onClick={() => setNotice(undefined)}>
            <X size={15} />
          </button>
        </aside>
      ) : null}
    </div>
  );
}
