package com.example.douyinagent.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import com.example.douyinagent.MainActivity
import com.example.douyinagent.accessibility.DouyinAccessibilityService
import com.example.douyinagent.capture.ScreenCaptureManager
import com.example.douyinagent.data.AgentPreferences
import com.example.douyinagent.execution.DouyinPage
import com.example.douyinagent.execution.TaskCoordinator
import com.example.douyinagent.model.AgentTask
import com.example.douyinagent.network.AgentApiClient
import com.example.douyinagent.network.AgentApiException
import com.example.douyinagent.runtime.AgentRuntime
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class AgentForegroundService :
    Service(),
    AgentRuntime.Listener,
    TaskCoordinator.Reporter {
    private lateinit var preferences: AgentPreferences
    private lateinit var api: AgentApiClient
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private val networkBusy = AtomicBoolean(false)
    private val metricsBusy = AtomicBoolean(false) // a manual 回采 is in progress
    private var ticker: ScheduledFuture<*>? = null
    private var currentTask: AgentTask? = null
    private var coordinator: TaskCoordinator? = null
    private var lastHeartbeatAt = 0L
    private var lastLeaseRenewAt = 0L
    private var lastSelectorsAt = 0L
    private var accountReadAttempted = false
    private var groupsReadAttempted = false
    private var xhsAccountReadAttempted = false
    private var reportedAnomaly: String? = null
    private val screenCapture by lazy { ScreenCaptureManager(this) }

    /**
     * 进程级的最后一道网：任何后台线程抛出未捕获异常时，至少让它**留下痕迹**，
     * 并把手上那条任务收掉。
     *
     * 为什么需要它：这个 Agent 用裸 `Thread {}` 跑发布和群发流程，
     * 而 Kotlin/Java 的线程默认行为是"异常打印到 stderr 然后线程静静地死"。
     * 在 Android 上 stderr 甚至不一定进 logcat —— 于是表现就是**什么都没发生**：
     * 状态机停在半路、手机被占着、日志停在最后一条正常记录、控制台一片绿。
     * 2026-09-09 云机6015 白占 32 分钟就是这么来的，而且报出来的原因还是错的。
     *
     * 每个线程自己的 try/catch 是主防线（能给出准确的失败原因），
     * 这里是兜底：以后有人再加一个裸线程，它也不会无声消失。
     */
    private fun installCrashNet() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            try {
                android.util.Log.e(
                    "AgentCrash", "线程 ${thread.name} 未捕获异常", error,
                )
                updateLocalStatus("后台线程出错：${error.javaClass.simpleName}")
                // 手上有任务就收掉，别让它挂着占手机
                currentTask?.let {
                    status(
                        "failed", "agent_crashed", 0,
                        "Agent 后台线程未捕获异常（${thread.name}）：" +
                            "${error.javaClass.simpleName}${error.message?.let { m -> "：$m" } ?: ""}",
                    )
                }
            } catch (_: Throwable) {
                // 兜底里再抛就真没救了，闭嘴让它走默认流程
            }
            previous?.uncaughtException(thread, error)
        }
    }

    override fun onCreate() {
        super.onCreate()
        preferences = AgentPreferences(this)
        api = createApi()
        AgentRuntime.listener = this
        installCrashNet()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification("正在连接后端"))
        updateLocalStatus("Agent 正在启动")
        ticker = executor.scheduleWithFixedDelay(::tickSafely, 0, 4, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_SCREEN_CAPTURE -> {
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
                val data = intent.getParcelableExtra<Intent>(EXTRA_RESULT_DATA)
                if (resultCode != 0 && data != null) {
                    val ok = runCatching { screenCapture.start(resultCode, data) }
                        .getOrDefault(false)
                    updateLocalStatus(if (ok) "屏幕截图已授权" else "屏幕截图授权失败")
                }
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun tickSafely() {
        // Cheap & network-free: confirm any pending 录屏 consent dialog every tick.
        // The event-time handler can miss it (dialog text not ready yet on EMUI),
        // and the dialog emits no further events — so poll here too (within 4s).
        runCatching { AgentRuntime.accessibilityService?.confirmCaptureConsent() }
        if (!networkBusy.compareAndSet(false, true)) return
        try {
            ensureRegistered()
            val now = System.currentTimeMillis()
            // Refresh the externalized Douyin selectors (anti-改版) on start then
            // periodically; failures keep the built-in defaults.
            if (lastSelectorsAt == 0L || now - lastSelectorsAt >= SELECTORS_INTERVAL_MS) {
                api.fetchSelectors()?.let {
                    com.example.douyinagent.accessibility.Selectors.douyin = it
                    if (currentTask == null) {
                        com.example.douyinagent.accessibility.Selectors.usePlatform("douyin")
                    }
                }
                lastSelectorsAt = now
            }
            if (now - lastHeartbeatAt >= HEARTBEAT_INTERVAL_MS) {
                val a11yOk = AgentRuntime.accessibilityService != null
                val reply = api.heartbeat(
                    preferences.deviceId,
                    currentTask?.id,
                    preferences.douyinNickname.ifBlank { null },
                    preferences.douyinId.ifBlank { null },
                    accessibilityOk = a11yOk,
                )
                val refreshMetrics = reply.refreshMetrics
                lastHeartbeatAt = now
                // 设备卡片「更新账号」— 强制重读本机登录的账号：清空缓存的 id（绕过
                // "只在没缓存时读"的门槛）+ 复位读取标记，下个空闲 tick 就重读并回传。
                if (reply.refreshAccount) {
                    preferences.douyinId = ""
                    preferences.douyinNickname = ""
                    accountReadAttempted = false
                    xhsAccountReadAttempted = false
                    // 账号与其内部群是一体的：换号/更新账号时群列表也要重扫，否则
                    // 群推送目标还停在开机那次的旧群。复位扫群标志，下个空闲 tick 重扫。
                    groupsReadAttempted = false
                    updateLocalStatus("收到「更新账号」指令，重新识别本机账号 + 重扫群…")
                }
                // Operator clicked 更新数据 — clear the daily 回采 gate so the idle
                // ticks re-collect 抖音 + 小红书 metrics. The gate STAYS cleared until
                // a 回采 actually runs (robust: a flag consumed while a11y wasn't
                // ready isn't "lost", unlike a one-shot worker trigger).
                if (refreshMetrics) {
                    preferences.lastXhsMetricsAt = 0L
                    preferences.lastDouyinMetricsAt = 0L
                    // Re-read accounts too, so login state refreshes after the
                    // operator logs an account in/out on the phone.
                    accountReadAttempted = false
                    xhsAccountReadAttempted = false
                    updateLocalStatus("收到「更新数据」指令，开始回采数据…")
                }
                updateLocalStatus(
                    currentTask?.let { "执行任务 #${it.id}：${it.name}" } ?: "设备在线，等待任务",
                )
            }

            val task = currentTask
            if (task == null) {
                // Don't claim a task the agent can't actually execute — if the
                // accessibility service isn't bound (common trap on A13: setting
                // set but not bound until reboot), claiming would just fail +
                // release. Report 未就绪 and wait; the matrix shows 仅在线.
                if (AgentRuntime.accessibilityService == null) {
                    updateLocalStatus("无障碍未绑定，请重启手机后重试（暂不领取任务）")
                } else {
                api.claim(preferences.deviceId)?.let(::startTask)
                // Once, while idle, identify the account; then read the groups.
                if (!accountReadAttempted &&
                    preferences.douyinId.isBlank() &&
                    AgentRuntime.accessibilityService != null
                ) {
                    accountReadAttempted = true
                    readAccountSequence()
                } else if (!groupsReadAttempted &&
                    currentTask == null &&
                    AgentRuntime.accessibilityService != null
                ) {
                    groupsReadAttempted = true
                    readGroupsSequence()
                } else if (!xhsAccountReadAttempted &&
                    currentTask == null &&
                    AgentRuntime.accessibilityService != null &&
                    isPackageInstalled(DouyinAccessibilityService.XHS_PACKAGE)
                ) {
                    xhsAccountReadAttempted = true
                    readXhsAccountSequence()
                } else if (now - preferences.lastXhsMetricsAt >= METRICS_INTERVAL_MS &&
                    currentTask == null &&
                    AgentRuntime.accessibilityService != null &&
                    isPackageInstalled(DouyinAccessibilityService.XHS_PACKAGE)
                ) {
                    // Persisted gate → reinstall/restart doesn't re-回采 within a day.
                    preferences.lastXhsMetricsAt = now
                    runMetricsOffTicker("xhs") // 每日回采小红书内容数据（阅读/点赞）
                } else if (now - preferences.lastDouyinMetricsAt >= METRICS_INTERVAL_MS &&
                    currentTask == null &&
                    AgentRuntime.accessibilityService != null
                ) {
                    preferences.lastDouyinMetricsAt = now
                    runMetricsOffTicker("douyin") // 每日回采抖音作品数据（播放量）
                }
                } // end else (accessibility bound)
            } else {
                if (now - lastLeaseRenewAt >= LEASE_RENEW_INTERVAL_MS) {
                    api.renewLease(task, preferences.deviceId)
                    lastLeaseRenewAt = now
                }
                // Re-drive the current screen so a missed tap on a static page
                // gets retried even when no accessibility event fires.
                AgentRuntime.accessibilityService?.reevaluate()
            }
        } catch (error: AgentApiException) {
            if (error.statusCode == 401 || error.statusCode == 404) {
                // 401 = token rejected; 404 = the device row was removed on the
                // backend (e.g. deleted from the 账号矩阵). Either way the stored
                // id/token is stale — drop it and re-register with the provision key
                // on the next tick, so a deleted-by-mistake device self-heals.
                preferences.clearRegistration()
                api = createApi()
                updateLocalStatus("设备凭证失效或已被移除，正在重新注册")
            } else if (error.statusCode == 409 && currentTask != null) {
                updateLocalStatus("任务租约已结束，等待下一条任务")
                currentTask = null
                coordinator = null
            } else {
                updateLocalStatus("后端请求失败：HTTP ${error.statusCode}")
            }
        } catch (error: Throwable) {
            updateLocalStatus("连接失败：${error.message ?: error.javaClass.simpleName}")
        } finally {
            networkBusy.set(false)
        }
    }

    private fun ensureRegistered() {
        if (preferences.deviceId > 0 && preferences.agentToken.isNotBlank()) return
        val registered = api.register(
            deviceCode = preferences.deviceCode,
            name = preferences.deviceName,
            androidVersion = Build.VERSION.RELEASE,
            douyinVersion = packageVersion(DouyinAccessibilityService.DOUYIN_PACKAGE),
            provisionKey = preferences.provisionKey,
        )
        preferences.deviceId = registered.id
        preferences.agentToken = registered.token
        updateLocalStatus("设备注册成功 #${registered.id}")
    }

    /**
     * One-time: open Douyin, go to the "我" tab, read the logged-in account
     * (nickname + 抖音号) and report it. Runs on the ticker thread; the short
     * sleeps are acceptable for a single startup identification.
     */
    private fun readAccountSequence() {
        val service = AgentRuntime.accessibilityService ?: return
        try {
            updateLocalStatus("正在识别登录的抖音账号…")
            signalSwitch("douyin")
            packageManager
                .getLaunchIntentForPackage(DouyinAccessibilityService.DOUYIN_PACKAGE)
                ?.let {
                    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(it)
                    Thread.sleep(3500)
                }
            service.actions.tapMeTab() // safe nav (contains-match on 我 → 乱点)
            Thread.sleep(2500)
            val info = service.readAccount()
            val id = info.douyinId
            val nickname = info.nickname
            if (!id.isNullOrBlank()) {
                preferences.douyinId = id
                if (!nickname.isNullOrBlank()) {
                    preferences.douyinNickname = nickname
                }
                api.heartbeat(
                    preferences.deviceId,
                    null,
                    preferences.douyinNickname.ifBlank { null },
                    id,
                )
                api.reportAccounts(
                    preferences.deviceId,
                    listOf(
                        AgentApiClient.AccountReport(
                            "douyin", preferences.douyinNickname.ifBlank { null }, id, true,
                        ),
                    ),
                )
                updateLocalStatus("已绑定抖音账号：${nickname ?: ""}（$id）")
            } else if (info.loggedIn == false) {
                // Detected a login prompt → report 未登录 so the matrix flags it.
                api.reportAccounts(
                    preferences.deviceId,
                    listOf(AgentApiClient.AccountReport("douyin", null, null, false)),
                )
                updateLocalStatus("抖音未登录，请在该设备登录抖音账号")
            } else {
                // Don't keep re-opening Douyin every idle tick (that's what made
                // it churn on the 消息/我 pages) — try once, then leave it.
                updateLocalStatus("未读到抖音账号，可在抖音「我」页停留后手动重启 Agent 重试")
            }
        } catch (error: Throwable) {
            updateLocalStatus("读取账号失败：${error.message}")
        } finally {
            returnToForeground()
        }
    }

    /**
     * One-time: open 小红书, go to its "我" tab, read 小红书号 + 昵称 and report
     * the account so the matrix shows the xhs account without manual setup.
     */
    private fun readXhsAccountSequence() {
        val service = AgentRuntime.accessibilityService ?: return
        try {
            updateLocalStatus("正在识别登录的小红书账号…")
            signalSwitch("xhs") // let the PC orchestrator open XHS via adb (MIUI-safe)
            packageManager
                .getLaunchIntentForPackage(DouyinAccessibilityService.XHS_PACKAGE)
                ?.let {
                    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(it)
                    Thread.sleep(3500)
                }
            service.actions.tapMeTab() // safe nav (contains-match on 我 → 乱点)
            Thread.sleep(2500)
            val info = service.readAccount("小红书号")
            if (!info.douyinId.isNullOrBlank()) {
                api.reportAccounts(
                    preferences.deviceId,
                    listOf(AgentApiClient.AccountReport("xhs", info.nickname, info.douyinId, true)),
                )
                updateLocalStatus("已绑定小红书账号：${info.nickname ?: ""}（${info.douyinId}）")
            } else if (info.loggedIn == false) {
                api.reportAccounts(
                    preferences.deviceId,
                    listOf(AgentApiClient.AccountReport("xhs", null, null, false)),
                )
                updateLocalStatus("小红书未登录，请在该设备登录小红书账号")
            } else {
                // Try once; don't re-open XHS every idle tick.
                updateLocalStatus("未读到小红书账号，可在小红书「我」页停留后重启 Agent 重试")
            }
        } catch (error: Throwable) {
            updateLocalStatus("读取小红书账号失败：${error.message}")
        } finally {
            returnToForeground()
        }
    }

    /**
     * Daily 回采: open the platform's profile, read each recent post's metrics
     * (阅读/点赞) from the grid, and report them. Runs idle, once per day.
     */
    private fun readMetricsSequence(platform: String) {
        val service = AgentRuntime.accessibilityService ?: return
        val appName = if (platform == "xhs") "小红书" else "抖音"
        val pkg =
            if (platform == "xhs") DouyinAccessibilityService.XHS_PACKAGE
            else DouyinAccessibilityService.DOUYIN_PACKAGE
        try {
            updateLocalStatus("正在回采${appName}内容数据…")
            signalSwitch(platform)
            packageManager.getLaunchIntentForPackage(pkg)?.let {
                it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(it)
                Thread.sleep(3500)
            }
            service.actions.tapMeTab() // safe nav (contains-match on 我 → 乱点)
            Thread.sleep(2500)
            val posts =
                if (platform == "xhs") collectXhsMetrics(service)
                else collectDouyinMetrics(service)
            android.util.Log.i("RecapTrace", "$platform collected=${posts.size}")
            if (posts.isNotEmpty()) {
                api.reportMetrics(preferences.deviceId, platform, posts)
                android.util.Log.i("RecapTrace", "$platform reported ${posts.size}")
                updateLocalStatus("已回采${appName} ${posts.size} 条内容数据")
            } else {
                updateLocalStatus("未回采到${appName}内容数据")
            }
        } catch (error: Throwable) {
            updateLocalStatus("回采失败：${error.message}")
        } finally {
            returnToForeground()
        }
    }

    /**
     * 小红书: walk the 我-grid (title + 阅读 + 赞 from each note's content-desc),
     * and open each recent note's detail to also read 收藏 + 评论 + 浏览 (the grid
     * never exposes those). Opens by tapping the note's title; backs out after.
     */
    private fun collectXhsMetrics(
        service: DouyinAccessibilityService,
    ): List<DouyinAccessibilityService.PostMetricInfo> {
        val merged = LinkedHashMap<String, DouyinAccessibilityService.PostMetricInfo>()
        val processed = HashSet<String>()
        // XHS may resume on a note-detail / sub-page (no 我 bottom-nav) or show a
        // "发现新版本" upgrade dialog — neither has the profile grid. Recover to a
        // main tab (首页+我 both visible) by dismissing the dialog / backing out,
        // then open the profile.
        repeat(6) {
            val root = service.rootInActiveWindow
            val pkg = root?.packageName?.toString()
            val onMain = service.actions.hasText(root, "首页") && service.actions.hasText(root, "我")
            if (pkg != DouyinAccessibilityService.XHS_PACKAGE) {
                // XHS not foreground (orchestrator launch race / MIUI blocked our
                // background start) — re-signal the PC orchestrator and retry launch.
                signalSwitch("xhs")
                packageManager.getLaunchIntentForPackage(DouyinAccessibilityService.XHS_PACKAGE)?.let {
                    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    runCatching { startActivity(it) }
                }
                Thread.sleep(2000)
            } else if (service.actions.hasText(root, "发现新版本")) {
                service.actions.clickAnyText(root, listOf("稍后再说", "以后再说", "取消"))
                Thread.sleep(900)
            } else if (onMain) {
                return@repeat
            } else {
                service.actions.back() // a sub-page within XHS (e.g. note detail)
                Thread.sleep(900)
            }
        }
        // Open the profile by tapping the 我 bottom-nav. Match its EXACT desc "我"
        // (a "contains" match or a fixed coordinate would hit a feed note instead).
        service.actions.tapExact(service.rootInActiveWindow, "我")
        Thread.sleep(2500)
        repeat(4) { // ~4 grid screens of recent notes
            if (service.actions.hasText(service.rootInActiveWindow, "发现新版本")) {
                service.actions.clickAnyText(service.rootInActiveWindow, listOf("稍后再说", "以后再说", "取消"))
                Thread.sleep(900)
            }
            val gridNotes = service.readPostMetrics("xhs")
            android.util.Log.i("RecapTrace", "xhs gridNotes=${gridNotes.size}")
            for (g in gridNotes) {
                if (g.title in processed) continue
                processed.add(g.title)
                if (service.actions.tapByText(service.rootInActiveWindow, listOf(g.title))) {
                    Thread.sleep(2600)
                    val d = service.readXhsNoteDetail()
                    android.util.Log.i(
                        "XHSMetrics",
                        "opened '${g.title}' -> detail=${d?.let { "浏览${it.views} 赞${it.likes} 藏${it.collects} 评${it.comments}" } ?: "null"}",
                    )
                    merged[g.title] = if (d != null) {
                        DouyinAccessibilityService.PostMetricInfo(
                            g.title,
                            if (d.views > 0) d.views else g.views,
                            if (d.likes > 0) d.likes else g.likes,
                            d.collects,
                            d.comments,
                            d.body,
                        )
                    } else {
                        g
                    }
                    service.actions.back()
                    Thread.sleep(1600)
                } else {
                    merged[g.title] = g // couldn't open — keep grid-only metrics
                }
            }
            service.actions.swipeUp()
            Thread.sleep(1300)
        }
        return merged.values.toList()
    }

    /**
     * 抖音: open the 作品 detail pager (我 → 作品 tab → first work) and swipe through,
     * reading the focused work's 浏览/喜欢/评论/收藏 + caption from each screen.
     */
    private fun collectDouyinMetrics(
        service: DouyinAccessibilityService,
    ): List<DouyinAccessibilityService.PostMetricInfo> {
        // 抖音 metrics via 创作者中心 → 数据分析 → 作品分析 — a structured DATA page
        // (each work = 标题 + 发布时间 + 播放/点赞/评论/收藏 as real text nodes), NOT
        // the 我-grid (image covers, per-work taps, splash/tree race, 乱点 risk).
        //
        // STATE-TOLERANT navigation: 抖音 can RESUME on ANY page (the 我 page, the
        // FEED, or — from a previous 回采 — the 创作者中心 itself). So don't assume a
        // start page: detect where we are each step and advance toward 作品分析.
        var nudged = false
        var reached = false
        var step = 0
        val popups = listOf("以后再说", "稍后再说", "我知道了", "知道了", "取消", "关闭", "跳过")
        while (step < 36 && !reached) {
            val root = service.rootInActiveWindow
            val pkg = root?.packageName?.toString()
            when {
                pkg != DouyinAccessibilityService.DOUYIN_PACKAGE -> {
                    // not foreground — nudge the orchestrator ONCE, then just wait
                    // (never re-startActivity ourselves: that restarts it to splash).
                    if (!nudged && step >= 4) { signalSwitch("douyin"); nudged = true }
                }
                // 作品分析 list present (per-work 播放+收藏 + parseable) → done.
                service.actions.hasText(root, "播放") &&
                    service.actions.hasText(root, "收藏") &&
                    service.readDouyinCreatorMetrics().isNotEmpty() -> reached = true
                // 创作者中心 main → tap 数据分析 的「更多」to open 作品分析.
                service.actions.hasText(root, "数据分析") -> {
                    service.actions.tapByText(root, listOf("更多"))
                    Thread.sleep(3500)
                }
                // 我 page → ☰ menu → 创作者中心.
                service.actions.hasText(root, "编辑主页") -> {
                    service.actions.tapTopRightCorner()
                    Thread.sleep(1800)
                    service.actions.clickTextAnyWindow(listOf("抖音创作者中心", "创作者中心"))
                    Thread.sleep(4000)
                }
                // some other 抖音 page (feed/popup) → dismiss + go to 我.
                else -> {
                    service.actions.clickAnyText(root, popups)
                    service.actions.tapMeTab()
                }
            }
            Thread.sleep(1200)
            step++
        }
        android.util.Log.i("RecapTrace", "dy reached作品分析=$reached step=$step")
        if (!reached) return emptyList()
        val merged = LinkedHashMap<String, DouyinAccessibilityService.PostMetricInfo>()
        var stale = 0
        repeat(8) { // scroll through ~近一周 works
            val before = merged.size
            service.readDouyinCreatorMetrics().forEach { merged[it.title] = it }
            android.util.Log.i("RecapTrace", "dy creator merged=${merged.size}")
            if (merged.size == before) stale++ else stale = 0
            if (stale >= 2) return@repeat
            service.actions.swipeUp()
            Thread.sleep(1500)
        }
        // Return to the 我 page (back out of the creator-center web pages, then 我) —
        // leaves a clean state so the NEXT 回采 doesn't resume deep in the analysis.
        repeat(3) { service.actions.back(); Thread.sleep(700) }
        service.actions.tapMeTab()
        Thread.sleep(500)
        return merged.values.toList()
    }

    private fun isPackageInstalled(pkg: String): Boolean =
        runCatching { packageManager.getLaunchIntentForPackage(pkg) != null }
            .getOrDefault(false)

    private fun startTask(task: AgentTask) {
        currentTask = task
        lastLeaseRenewAt = System.currentTimeMillis()
        if (task.isGroupMessage) {
            coordinator = null
            updateLocalStatus("开始群发任务 #${task.id}")
            // Run on its own thread so the ticker keeps heartbeating + renewing
            // the lease (otherwise a multi-group send would expire the lease).
            Thread {
                try {
                    runBroadcast(task)
                } catch (error: Throwable) {
                    // 同 fillAndPublish：线程死了没人知道，任务就永远挂着。
                    status(
                        "failed", "broadcast_crashed", 0,
                        "群发过程中出错：${error.javaClass.simpleName}" +
                            "${error.message?.let { "：$it" } ?: ""}",
                    )
                }
            }.start()
            return
        }
        coordinator = TaskCoordinator(this)
        updateLocalStatus("已领取任务 #${task.id}：${task.name}")
        coordinator?.start(task)
    }

    /**
     * Group broadcast (首版：文字). For each target group: open 消息 → tap the
     * group → type the text → tap 发送 → screenshot. Image and @所有人 come next.
     * Runs on the ticker thread; deliberate sleeps pace each step.
     */
    /**
     * 等抖音真的到了**一级页面**（底部导航出现）。冷启动要 8~12 秒，不能靠固定 sleep。
     *
     * 判据和发布那条路（PageRecognizer 的 hasMainTabBar）保持一致 —— 以前这里
     * 单独写了一套更严的，是三类误判的来源：
     *
     * ① **只看 `rootInActiveWindow`**：页面切换刚发生时它还是上一个窗口（或 null），
     *    任何浮层（输入法、系统弹窗）成为活动窗口时它也不是抖音 —— 明明在屏幕上的
     *    页面会被判成"还没到"。代码库里早就有 `textsMatchingAnyWindow` 就是为这个。
     * ② **只找「首页」两个字**：发布那条路认的是"底部导航完整"（首页+我+其中一个
     *    次级 tab），任何一级页面都算 —— 用户明确纠正过「在个人主页也可以发布呀，
     *    只要是抖音的一级页面」。
     * ③ **等不到就干等到超时**：不 back、不重来。而抖音停在子页面（个人主页/视频详情/
     *    群聊）时，等一万年也等不出底部导航。
     *
     * 现在：全窗口找 → 认任何一级页面 → 等一半时间还没到就 back 几下爬出子页面。
     */
    private fun waitForDouyinHome(timeoutMs: Long): Boolean {
        val start = System.currentTimeMillis()
        val deadline = start + timeoutMs
        var backTried = false
        while (System.currentTimeMillis() < deadline) {
            val svc = AgentRuntime.accessibilityService
            if (svc != null) {
                // 和 PageRecognizer.hasMainTabBar 同一套判据：底部导航完整
                val hasPrimary = HOME_TABS_PRIMARY.all {
                    svc.actions.textsMatchingAnyWindow(it).isNotEmpty()
                }
                val hasSecondary = HOME_TABS_SECONDARY.any {
                    svc.actions.textsMatchingAnyWindow(it).isNotEmpty()
                }
                if (hasPrimary && hasSecondary) return true
                // 过了一半时间还没到：多半是被"恢复"到了子页面（个人主页/视频详情/
                // 群聊），底部导航根本不存在。往回退几步爬出来，再等剩下的时间。
                if (!backTried && System.currentTimeMillis() - start > timeoutMs / 2) {
                    backTried = true
                    log("info", "group_message", "抖音停在子页面，正在退回一级页面")
                    repeat(3) { svc.actions.back(); Thread.sleep(900) }
                }
            }
            Thread.sleep(700)
        }
        return false
    }

    /** 点到「消息」tab 并确认真的进了消息列表。点不中就重试，不假设它成功了。 */
    private fun openMessageList(
        svc: com.example.douyinagent.accessibility.DouyinAccessibilityService,
        timeoutMs: Long,
    ): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            // ⚠ 看返回值。以前这一下是 `svc.actions.tapBottomTab("消息")` 丢掉结果，
            // 点没点中都当点中了 —— 冷启动时它必然点不中（屏幕上还是开屏），
            // 然后后面十次翻找全在错的页面上进行。
            if (svc.actions.tapBottomTab("消息")) {
                Thread.sleep(1600)
                return true
            }
            Thread.sleep(900)
        }
        return false
    }

    private fun runBroadcast(task: AgentTask) {
        if (AgentRuntime.accessibilityService == null) {
            status("failed", "group_message", 0, "无障碍服务未连接，无法群发")
            return
        }
        status("running", "group_message", 10, "开始群发到 ${task.targetGroups.size} 个群")
        var imageReady = false
        if (task.hasImage) {
            val bytes = api.downloadTaskImage(task.id)
            imageReady = bytes != null && saveImageToGallery(bytes)
            log(
                "info",
                "group_message",
                if (imageReady) "图片已下发到相册" else "图片下发失败，本次仅发文字",
            )
            Thread.sleep(1500)
        }
        var sent = 0
        // ⚠ **必须是 `:cold`**（force-stop 再拉起），和发布那条路一致。
        // 裸的 "douyin" 只发 launcher intent，那是「恢复」不是「打开」——
        // 抖音上次停在哪一页就还在哪一页（launch_app 的注释实测过：会停在
        // 「我」的个人主页，那是子页面、**没有底部导航**），于是下面的
        // waitForDouyinHome 找不到一级页面，干等 20 秒判死，报「抖音没能打开到首页」。
        //
        // 2026-09-08 的现场：早班群发基本能过、晚班三条全灭 —— 因为早上抖音是干净的，
        // 恢复回来就是首页；到傍晚，白天的图文任务已经把它留在各种子页面里了。
        // 同一台机器，群发失败 40 秒后图文成功，差别就在这一个词。
        //
        // 冷启动会先显示旧缓存的群列表 —— 那正是下面 pullToRefresh 在解决的，
        // 不构成不用冷启动的理由。
        enterLaunchGrace()
        signalSwitch("douyin:cold")
        // 打开抖音→消息 只做一次，并下拉刷新强制同步(冷启动会先显示旧缓存列表)。之后每个
        // 群都在这个已同步的列表里查找，不再反复重启抖音(反复重启会反复冷启动→丢群)。
        run {
            packageManager
                .getLaunchIntentForPackage(DouyinAccessibilityService.DOUYIN_PACKAGE)
                ?.let {
                    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(it)
                }
            // ⚠ 这里以前是 `Thread.sleep(2500)` 然后直接点「消息」。
            // 抖音**冷启动**（被 force-stop 过、或刚开机）要 8~12 秒才出首页，
            // 2.5 秒时屏幕上还是开屏广告 —— 那一下「消息」根本没点中，
            // 后面十次翻找全都在错的页面上进行，最后报「未找到群」。
            // 而运营看到的是「怎么可能找不到群」，群明明在。
            // （云机6012 任务 #1616 就是这样：日志里「当前屏可见含『群』的文本：[]」，
            // 空数组正说明根本不在消息列表。手动重推一次就好，因为那时抖音已经热了。）
            // 改成**等到抖音真的到了一级页面**，最多等 20 秒。
            if (!waitForDouyinHome(20_000)) {
                status("failed", "group_message", 0, "抖音没能打开到首页，这次不推了")
                return
            }
            val svc = AgentRuntime.accessibilityService
            if (svc == null || !openMessageList(svc, 15_000)) {
                // 说清是"没打开消息列表"，不是"群没了" —— 两者的处理动作完全不同
                status("failed", "group_message", 0, "没能打开抖音的「消息」列表，这次不推了")
                return
            }
            svc.actions.pullToRefresh()
            Thread.sleep(2800)
        }
        for (group in task.targetGroups) {
            try {
                val svc = AgentRuntime.accessibilityService ?: break
                // 关键：回到「主消息列表」。发完上一个群后可能还在群聊里/键盘没收/@选择器残留，
                // 底部「消息」tab 只在主页面才有——所以 back 到底部 tab 重新出现为止，再点它。
                var onList = false
                for (b in 0 until 5) {
                    if (svc.actions.tapBottomTab("消息")) { onList = true; break }
                    svc.actions.back()
                    Thread.sleep(1100)
                }
                if (!onList) svc.actions.tapBottomTab("消息")
                Thread.sleep(1500)
                repeat(4) { svc.actions.scrollUp(); Thread.sleep(400) }
                // 逐屏下滑查找目标群(可能在列表下方)。
                var opened = false
                for (attempt in 0 until 10) {
                    // 用 ACTION_CLICK 点会话行（clickAnyText 会往上找可点击的父节点），
                    // 不要用坐标点：列表刚下拉刷新/滚动完还在回弹，tapByText 拿的是上一帧
                    // 的 bounds，点下去会落到相邻那一行 —— 云机6012 就是这样点进了「安心」
                    // 的私聊。坐标点只作为兜底。
                    // 只用 ACTION_CLICK 按节点身份点，**不再用坐标兜底**：坐标是上一帧的，
                    // 列表回弹时会点到隔壁那一行（云机6012 就这样进过「安心」的私聊）。
                    // 找不到就滑一屏再找，宁可找不到也不能点错。
                    val hit = svc.actions.clickAnyText(svc.rootInActiveWindow, listOf(group))
                    if (hit) {
                        Thread.sleep(2200)
                        if (inChatWith(svc, group)) {
                            opened = true
                            break
                        }
                        // 点开的不是目标会话 → 退回去重试。绝不能"以为在群里"继续往下发：
                        // 那会把群发内容发进别人的私聊，比发失败严重得多。
                        log("warning", "group_message", "点开的不是「$group」，退回重试")
                        svc.actions.back()
                        Thread.sleep(1200)
                    }
                    svc.actions.swipeUp()
                    Thread.sleep(1200)
                }
                if (!opened) {
                    val seen = svc.actions
                        .textsMatching(svc.rootInActiveWindow, "群")
                        .take(12)
                        .joinToString(" ｜ ")
                    log("warning", "group_message", "未找到群：$group｜当前屏可见含「群」的文本：[$seen]")
                    continue
                }
                Thread.sleep(2500)
                if (imageReady) {
                    // 更多面板 → 相册 → 选最近一张 → 发送（相册选图细节真机迭代调整）
                    svc.actions.tapByText(svc.rootInActiveWindow, listOf("更多面板"))
                    Thread.sleep(1800)
                    svc.actions.clickAnyText(svc.rootInActiveWindow, listOf("相册")) ||
                        svc.actions.tapByText(svc.rootInActiveWindow, listOf("相册"))
                    Thread.sleep(2800)
                    // 相册第一张（最近=刚下发的图）通常在左上角
                    svc.actions.tapFraction(0.13f, 0.28f)
                    Thread.sleep(1500)
                    svc.actions.clickAnyText(
                        svc.rootInActiveWindow,
                        listOf("发送", "确定", "完成", "下一步"),
                    )
                    Thread.sleep(2500)
                    screenshot("group_img_$group")
                }
                val hints = CHAT_INPUT_HINTS
                // Tap the input to focus it + raise the keyboard before typing.
                svc.actions.tapByText(svc.rootInActiveWindow, hints)
                Thread.sleep(2000)
                val filled: Boolean
                if (task.mentionAll) {
                    // Real @所有人: (1) type the body first (setText handles Chinese),
                    // (2) move the cursor to position 0, (3) ask the orchestrator to
                    // inject a REAL "@" keystroke there — that pops 抖音's @-member
                    // picker (accessibility setText does NOT, which is why @所有人 kept
                    // failing), (4) tap 所有人 → the mention chip is inserted BEFORE the
                    // body → "@所有人 <body>". Falls back to literal "@所有人 " text if
                    // the picker still doesn't come up, so the message always sends.
                    val bodyOk = svc.actions.fillField(svc.rootInActiveWindow, hints, task.body)
                    Thread.sleep(1200)
                    svc.actions.setSelectionStart(svc.rootInActiveWindow, hints)
                    Thread.sleep(600)
                    signalInput("@") // orchestrator: adb shell input text "@"
                    Thread.sleep(3000)
                    val picked = svc.actions.clickTextAnyWindow(listOf("所有人"))
                    Thread.sleep(1500)
                    if (picked) {
                        filled = bodyOk
                        log("info", "group_message", "已@所有人（真·成员提醒）并输入正文")
                    } else {
                        // Picker never appeared → prepend literal text so it still sends.
                        filled = svc.actions.fillField(
                            svc.rootInActiveWindow, hints, "@所有人 " + task.body,
                        )
                        log("info", "group_message", "@所有人选择器未命中，用文字方式补上")
                    }
                } else {
                    filled = svc.actions.fillField(svc.rootInActiveWindow, hints, task.body)
                }
                Thread.sleep(2500)
                val ok = svc.actions.clickAnyText(svc.rootInActiveWindow, listOf("发送")) ||
                    svc.actions.tapByText(svc.rootInActiveWindow, listOf("发送"))
                Thread.sleep(2500)
                screenshot("group_$group")
                if (filled && ok) {
                    sent += 1
                    log("info", "group_message", "已发送到群：$group")
                } else {
                    log(
                        "warning",
                        "group_message",
                        "群「$group」可能未发送（填字=$filled，发送=$ok）",
                    )
                }
                svc.actions.back()
                Thread.sleep(1000)
            } catch (error: Throwable) {
                log("warning", "group_message", "群「$group」异常：${error.message}")
            }
        }
        if (sent > 0) {
            status("succeeded", "completed", 100, "群发完成：成功 $sent/${task.targetGroups.size} 个群")
        } else {
            status("failed", "completed", 0, "群发未成功，请人工检查截图")
        }
    }

    private fun saveImageToGallery(bytes: ByteArray): Boolean {
        return try {
            val values = android.content.ContentValues().apply {
                put(
                    android.provider.MediaStore.Images.Media.DISPLAY_NAME,
                    "dyagent_${System.currentTimeMillis()}.jpg",
                )
                put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures")
                    put(android.provider.MediaStore.Images.Media.IS_PENDING, 1)
                }
            }
            val resolver = contentResolver
            val uri = resolver.insert(
                android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                values,
            ) ?: return false
            resolver.openOutputStream(uri)?.use { it.write(bytes) }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.clear()
                values.put(android.provider.MediaStore.Images.Media.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            }
            true
        } catch (_: Throwable) {
            false
        }
    }

    private fun readGroupsSequence() {
        val service = AgentRuntime.accessibilityService ?: return
        try {
            updateLocalStatus("正在读取群列表…")
            signalSwitch("douyin")
            packageManager
                .getLaunchIntentForPackage(DouyinAccessibilityService.DOUYIN_PACKAGE)
                ?.let {
                    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(it)
                    Thread.sleep(2500)
                }
            // 冷启动可能停在开屏/信息流：重试点「消息」直到真正进入主消息列表(开屏动画
            // 期间没有「消息」tab，naive 的一次点击会点空→readGroups 读到开屏→0 个群)。
            var onList = false
            for (attempt in 0 until 6) {
                if (service.actions.tapBottomTab("消息")) { onList = true; break }
                Thread.sleep(1500)
            }
            Thread.sleep(2200)
            // 最多 3 轮「下拉刷新→回顶→逐屏累积读取」；读到群就停。下拉刷新强制服务器同步
            // (冷启动先显示旧缓存/空列表，是「有群却读到0」的主因之一)；慢网络下一次刷新可能
            // 没同步完，所以空读就再刷一次。逐屏下滑累积——群可能在列表下方，单屏会漏。
            val merged = LinkedHashMap<String, DouyinAccessibilityService.GroupInfo>()
            for (round in 0 until 3) {
                repeat(3) { service.actions.scrollUp(); Thread.sleep(250) } // 先回顶再刷新
                service.actions.pullToRefresh()
                Thread.sleep(if (round == 0) 2800 else 3500)
                repeat(3) { service.actions.scrollUp(); Thread.sleep(250) } // 刷新后回到顶部
                for (screen in 0 until 8) {
                    service.readGroups().forEach { g -> merged.putIfAbsent(g.name, g) }
                    service.actions.swipeUp()
                    Thread.sleep(850)
                }
                service.readGroups().forEach { g -> merged.putIfAbsent(g.name, g) }
                if (merged.isNotEmpty()) break
            }
            val groups = merged.values.toList()
            android.util.Log.i(
                "GroupScan",
                "onList=$onList merged=${groups.size} names=${groups.joinToString(",") { it.name }}",
            )
            if (groups.isNotEmpty()) {
                api.syncGroups(preferences.deviceId, groups)
                updateLocalStatus("已读取 ${groups.size} 个群并同步（onList=$onList）")
            } else {
                updateLocalStatus("未读到群：消息列表无群聊，或未进入列表(onList=$onList)")
            }
        } catch (error: Throwable) {
            updateLocalStatus("读取群失败：${error.message}")
        } finally {
            returnToForeground()
        }
    }

    /**
     * Bring our own MainActivity back to the front so the agent idles on its own
     * screen (its "home base") rather than leaving Douyin open. Done via the
     * accessibility HOME action first (reliable on MIUI) then launching our
     * activity, which is allowed because we hold SYSTEM_ALERT_WINDOW.
     */
    private fun returnToForeground() {
        try {
            AgentRuntime.accessibilityService?.performGlobalAction(
                android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME,
            )
            Thread.sleep(400)
            startActivity(
                Intent(this, MainActivity::class.java).addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_REORDER_TO_FRONT,
                ),
            )
        } catch (_: Throwable) {
            // Best effort; not being able to foreground is non-fatal.
        }
    }

    private fun createApi(): AgentApiClient =
        AgentApiClient(preferences.baseUrl) { preferences.agentToken }

    private var a11yDropWindowStart = 0L
    private var a11yDropCount = 0

    /**
     * 拉起/冷启动抖音之后的一小段时间里，无障碍被系统重建是**正常**的：
     * force-stop + 冷启动会让系统重建无障碍服务，抖音自己也会写
     * `enabled_accessibility_services`（写它就会重启列表里所有无障碍服务，
     * 见 tools/agent_orchestrator.py 的 _reassert_a11y）。
     *
     * 这段时间的掉线**不能算进熔断** —— 否则熔断器打死的正是它本该保护的任务。
     * 2026-09-08 实测：群发任务开始后 +1s/+5s/+11s 各掉一次，刚好凑满 3 次。
     * 掉线照样记日志，只是不计数。
     */
    private var a11yGraceUntil = 0L

    private fun enterLaunchGrace(ms: Long = LAUNCH_GRACE_MS) {
        a11yGraceUntil = System.currentTimeMillis() + ms
    }

    override fun onAccessibilityReady() {
        updateLocalStatus("无障碍服务已连接")
        currentTask?.let {
            log("info", "accessibility_ready", "无障碍服务已连接")
        }
    }

    override fun onAccessibilityStopped() {
        updateLocalStatus("无障碍服务未启用，任务将暂停")
        val task = currentTask ?: return
        log("warning", "accessibility_stopped", "无障碍服务已断开")

        // 熔断：无障碍反复被系统重建时，这个任务已经不可能做成了 —— 每次重建
        // 都读不到屏幕，页面识别一路空转。以前的行为是硬撑到 6 分钟超时，
        // 期间还一直占着这台手机（任务 #1595 就是这么浪费掉的），而最后报的原因
        // 是「页面无法识别」，和真实情况对不上。
        val now = System.currentTimeMillis()
        if (now < a11yGraceUntil) {
            // 刚拉起抖音，这一阵的重建是预期内的，不计入熔断
            return
        }
        if (now - a11yDropWindowStart > A11Y_DROP_WINDOW_MS) {
            a11yDropWindowStart = now
            a11yDropCount = 0
        }
        a11yDropCount++
        if (a11yDropCount >= A11Y_DROP_LIMIT) {
            a11yDropCount = 0
            status(
                "failed", "accessibility_unstable", 0,
                "无障碍服务反复中断（${A11Y_DROP_WINDOW_MS / 1000} 秒内 $A11Y_DROP_LIMIT 次），" +
                    "这台手机现在做不了任务，已提前结束",
            )
            log("warning", "accessibility_unstable", "任务 #${task.id} 因无障碍反复中断提前结束")
        }
    }

    override fun onPageChanged(page: DouyinPage) {
        val task = currentTask ?: return
        val service = AgentRuntime.accessibilityService ?: return
        coordinator?.onPage(task, page, service.rootInActiveWindow, service.actions)
    }

    override fun onAnomalyDetected(message: String) {
        // Report each distinct anomaly once: flag the account abnormal (which
        // pauses auto-publish server-side) and fail the running task.
        if (message == reportedAnomaly) return
        reportedAnomaly = message
        updateLocalStatus("检测到账号异常：$message，已暂停该账号发布")
        executor.execute {
            try {
                api.heartbeat(
                    preferences.deviceId,
                    currentTask?.id,
                    preferences.douyinNickname.ifBlank { null },
                    preferences.douyinId.ifBlank { null },
                    "abnormal",
                    message,
                    platform = currentTask?.platform ?: "douyin",
                )
            } catch (_: Throwable) {
                // next heartbeat / re-detection will retry
            }
        }
        if (currentTask != null) {
            status("failed", "account_abnormal", 0, "账号异常：$message")
        }
    }

    override fun launchApp(platform: String) {
        val pkg =
            if (platform == "xhs") DouyinAccessibilityService.XHS_PACKAGE
            else DouyinAccessibilityService.DOUYIN_PACKAGE
        val appName = if (platform == "xhs") "小红书" else "抖音"
        val launchIntent = packageManager.getLaunchIntentForPackage(pkg)
        if (launchIntent == null) {
            status("failed", "launching_douyin", 10, "未检测到$appName App")
            return
        }
        // Tell the PC orchestrator (which tails logcat) to bring the target app to
        // the front via adb — reliable on MIUI where our own background start is
        // throttled. We also try ourselves below as a fallback.
        // `:cold` 让 PC 编排器先 force-stop 再拉起 —— 见下面那段注释。
        // 更新数据/读账号那条路径发的是裸的 "douyin"，保持"恢复"语义不变
        // （那边拉起后只等 3.5 秒就点「我」，冷启动来不及）。
        enterLaunchGrace()
        signalSwitch(if (platform == "xhs") "xhs:cold" else "douyin:cold")
        // ⚠ 光 NEW_TASK 是"恢复"不是"打开"：抖音上次停在哪一页，恢复回来还是那一页。
        // 实测只发 launcher intent 会停在「我」的个人主页，页面识别器不认得，
        // 于是一路 UNKNOWN → 自动恢复 → 恢复不出来 → 卡到超时。
        // CLEAR_TASK 把任务栈清掉，回到首页。真正干净的冷启动要 `am force-stop`，
        // 那需要 shell 权限，由 PC 编排器做（见 tools/agent_orchestrator.py 的
        // launch_app）—— 这里是它没跑起来时的兜底。
        launchIntent.addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK,
        )
        startActivity(launchIntent)
    }

    /**
     * 回采必须跑在自己的线程上。
     *
     * 它以前直接跑在 4 秒 ticker 里，而 `tickSafely` 全程持有 `networkBusy`
     * （进入时 compareAndSet、finally 才释放）——一轮抖音回采光导航就有几十秒固定
     * sleep，期间**一次心跳都发不出去**。而后端 `device_offline_seconds = 45`：
     * 于是每轮回采都会把自己判成掉线，触发 critical 告警，运营看到就去点「检测设备」
     * 甚至刷机，而刷机会丢登录态。群发早就是另起线程的（runBroadcast），照抄。
     *
     * `metricsBusy` 保证同一时刻只有一轮回采在跑（手动触发的和每日定时的都走这里）。
     */
    private fun runMetricsOffTicker(platform: String) {
        if (!metricsBusy.compareAndSet(false, true)) return
        Thread {
            try {
                readMetricsSequence(platform)
            } catch (error: Throwable) {
                updateLocalStatus("回采${if (platform == "xhs") "小红书" else "抖音"}异常：${error.message}")
            } finally {
                metricsBusy.set(false)
            }
        }.start()
    }

    /** One-line logcat signal the PC-side orchestrator listens for (tag AgentSwitch). */
    private fun signalSwitch(target: String) {
        android.util.Log.i("AgentSwitch", target)
    }

    /**
     * 确认真的进了目标会话，再决定要不要往下发。两个条件都要满足：
     *  ① 页面上有输入框提示（发消息/按住说话/说点什么）——消息列表页没有，所以这条
     *    能区分"进了某个会话"和"还站在列表上"；
     *  ② 页面上出现群名——区分"进对了群"和"进错了别人的私聊"。长群名在标题栏可能被
     *    截断，所以名字取不到时退一步用前 6 个字再比一次。
     */
    private fun inChatWith(svc: DouyinAccessibilityService, group: String): Boolean {
        // 两个都不能少：① 输入框提示（真机上是「 发消息或按住说话...」，消息列表页没有）
        // 证明进了某个会话；② 页面上出现群名，证明进对了群。
        // 两个坑都踩过：查 `rootInActiveWindow` 在刚切页面时还指着消息列表 → 明明进了群
        // 却判"不是该群"，于是进去→退出→再进去地空转（云机6012）；所以跨所有窗口查，
        // 并且给页面最多 6 秒渲染出来，而不是看一眼就退回去。
        val prefix = if (group.length >= 6) group.take(6) else group
        repeat(6) {
            // ① 最强证据：同一个窗口里既有群名又有输入框。
            if (svc.actions.windowContains(listOf(group), CHAT_INPUT_HINTS)) return true
            if (svc.actions.windowContains(listOf(prefix), CHAT_INPUT_HINTS)) return true
            // ② 抖音群聊页的**标题根本不进无障碍树**（uiautomator 看得到，
            //    getWindows() 里没有），真机日志里就是「输入框=true 群名=false」。
            //    所以退一步只确认「这是一个群聊」：输入框 + 「N名群友」。
            //    进的是不是目标群，由点击方式保证 —— 我们是按节点身份 ACTION_CLICK
            //    点那一行的，不是按坐标点的，不存在点偏到隔壁行的可能；「名群友」
            //    再顺手排除误进私聊。
            if (svc.actions.windowContains(listOf("名群友"), CHAT_INPUT_HINTS)) return true
            Thread.sleep(1000)
        }
        val sawInput = CHAT_INPUT_HINTS.any {
            svc.actions.textsMatchingAnyWindow(it).isNotEmpty()
        }
        log(
            "warning",
            "group_message",
            "校验未通过：输入框=$sawInput｜屏上文本：" +
                svc.actions.windowTexts(12).joinToString(" ｜ ").take(400),
        )
        return false
    }

    /**
     * Ask the PC orchestrator to inject a REAL keystroke via `adb shell input text`.
     * Accessibility ACTION_SET_TEXT sets the field value WITHOUT firing 抖音's
     * text-watcher, so typing "@" that way never pops the @-member picker. A real
     * `input text` keystroke does (verified on 6003). Used for @所有人 in 群发.
     */
    private fun signalInput(text: String) {
        android.util.Log.i("AgentInput", text)
    }

    override fun status(
        status: String,
        step: String,
        progress: Int,
        message: String,
    ) {
        val task = currentTask ?: return
        executor.execute {
            try {
                api.updateStatus(
                    task = task,
                    deviceId = preferences.deviceId,
                    status = status,
                    step = step,
                    progress = progress,
                    message = message,
                    errorMessage = if (status == "failed") message else null,
                )
                updateLocalStatus(message)
                if (status == "succeeded") reportedAnomaly = null
                if (status in setOf("succeeded", "failed", "cancelled")) {
                    currentTask = null
                    coordinator = null
                    com.example.douyinagent.accessibility.Selectors.usePlatform("douyin")
                    // Leave Douyin and return to our own screen to idle. Signal
                    // the PC orchestrator to do it via adb (reliable on MIUI),
                    // and also try ourselves as a fallback.
                    signalSwitch("home")
                    returnToForeground()
                }
            } catch (error: Throwable) {
                updateLocalStatus("状态回传失败：${error.message}")
            }
        }
    }

    override fun log(level: String, step: String, message: String) {
        val task = currentTask ?: return
        executor.execute {
            try {
                api.appendLog(task, preferences.deviceId, level, step, message)
            } catch (_: Throwable) {
                // Heartbeat will expose connectivity loss; logs should not stop execution.
            }
        }
    }

    override fun screenshot(step: String) {
        val task = currentTask ?: return
        val service = AgentRuntime.accessibilityService
        // On Android 11+ PREFER the accessibility takeScreenshot API: it needs no
        // 录屏 permission and works on the cloud phones (A13) + Huawei where
        // MediaProjection is un-grantable / yields no frame ("无可用画面"). Fall
        // back to MediaProjection (the only option on Android 10 / MI 9).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && service != null) {
            service.captureScreenshot { result ->
                result
                    .onSuccess { uploadScreenshot(task, step, it.width, it.height, it.png) }
                    .onFailure { err -> captureViaProjection(task, step, err.message) }
            }
            return
        }
        // Android 10 (or no a11y service): MediaProjection only.
        captureViaProjection(task, step, null)
    }

    /** MediaProjection fallback. `priorError` = why the a11y path failed (if any). */
    private fun captureViaProjection(task: AgentTask, step: String, priorError: String?) {
        if (screenCapture.isReady) {
            screenCapture.capture { result ->
                result.onSuccess { uploadScreenshot(task, step, it.width, it.height, it.png) }
                    .onFailure { log("warning", step, "截图失败：${it.message}") }
            }
            return
        }
        log(
            "warning",
            step,
            priorError?.let { "截图失败：$it" }
                ?: "未截图：录屏未授权，请在 App 内点「授权屏幕截图（录屏）」",
        )
    }

    private fun uploadScreenshot(
        task: AgentTask,
        step: String,
        width: Int,
        height: Int,
        png: ByteArray,
    ) {
        executor.execute {
            try {
                api.uploadScreenshot(
                    task = task,
                    deviceId = preferences.deviceId,
                    step = step,
                    width = width,
                    height = height,
                    png = png,
                )
                api.appendLog(task, preferences.deviceId, "info", step, "关键步骤截图已回传")
            } catch (error: Throwable) {
                updateLocalStatus("截图回传失败：${error.message}")
            }
        }
    }

    private fun packageVersion(packageName: String): String? =
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                packageManager.getPackageInfo(
                    packageName,
                    PackageManager.PackageInfoFlags.of(0),
                ).versionName
            } else {
                @Suppress("DEPRECATION")
                packageManager.getPackageInfo(packageName, 0).versionName
            }
        } catch (_: PackageManager.NameNotFoundException) {
            null
        }

    private fun updateLocalStatus(message: String) {
        preferences.lastStatus = message
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, buildNotification(message))
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Agent 运行状态",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
    }

    private fun buildNotification(message: String) =
        android.app.Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("抖音发布 Agent")
            .setContentText(message)
            .setOngoing(true)
            .setContentIntent(
                PendingIntent.getActivity(
                    this,
                    0,
                    Intent(this, MainActivity::class.java),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                ),
            )
            .addAction(
                android.app.Notification.Action.Builder(
                    null,
                    "停止",
                    PendingIntent.getService(
                        this,
                        1,
                        Intent(this, AgentForegroundService::class.java).setAction(ACTION_STOP),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                    ),
                ).build(),
            )
            .build()

    override fun onDestroy() {
        AgentRuntime.listener = null
        ticker?.cancel(true)
        executor.shutdownNow()
        screenCapture.release()
        updateLocalStatus("Agent 已停止")
        super.onDestroy()
    }

    companion object {
        /** 群聊/私聊输入框的提示文案。真机上是「 发消息或按住说话...」（前面还带一个
         * U+2006 窄空格），所以只能按子串匹配，别拿整串去比。 */
        val CHAT_INPUT_HINTS = listOf("发消息", "按住说话", "说点什么")
        const val ACTION_START = "com.example.douyinagent.START"
        const val ACTION_STOP = "com.example.douyinagent.STOP"
        const val ACTION_SCREEN_CAPTURE = "com.example.douyinagent.SCREEN_CAPTURE"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        private const val CHANNEL_ID = "agent_runtime"
        private const val NOTIFICATION_ID = 2101
        private const val HEARTBEAT_INTERVAL_MS = 15_000L
        // 无障碍反复中断的熔断阈值：30 秒内断 3 次就别硬撑了
        // 和 SelectorConfig.homeTabsPrimary / homeTabsSecondary 同值。
        // 抄一份而不是引用，是因为这里跑在服务线程上、拿不到 Selectors.current 的
        // 生命周期保证；两边都改的时候记得一起改。
        private val HOME_TABS_PRIMARY = listOf("首页", "我")
        private val HOME_TABS_SECONDARY = listOf("消息", "朋友", "拍摄，按钮", "拍摄按钮")

        private const val A11Y_DROP_WINDOW_MS = 30_000L
        private const val A11Y_DROP_LIMIT = 3
        // 冷启动实测 8~12 秒，留到 25 秒覆盖慢的那几台
        private const val LAUNCH_GRACE_MS = 25_000L
        private const val SELECTORS_INTERVAL_MS = 600_000L // refresh selectors every 10 min
        private const val METRICS_INTERVAL_MS = 86_400_000L // 回采 once a day
        private const val LEASE_RENEW_INTERVAL_MS = 30_000L
    }
}
