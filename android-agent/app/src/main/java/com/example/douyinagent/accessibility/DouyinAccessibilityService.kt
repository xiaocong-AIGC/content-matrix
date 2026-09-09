package com.example.douyinagent.accessibility

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import com.example.douyinagent.execution.DouyinPage
import com.example.douyinagent.runtime.AgentRuntime
import java.io.ByteArrayOutputStream

class DouyinAccessibilityService : AccessibilityService() {
    private val recognizer = PageRecognizer()
    lateinit var actions: AccessibilityActions
        private set

    private var currentPage: DouyinPage = DouyinPage.UNKNOWN

    override fun onServiceConnected() {
        super.onServiceConnected()
        actions = AccessibilityActions(this)
        AgentRuntime.onAccessibilityReady(this)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Auto-confirm the screen-capture consent dialog (any package) so the
        // operator never has to tap "立即开始" each time projection is requested.
        if (autoConfirmCaptureDialog()) return
        val pkg = event?.packageName?.toString()
        if (pkg != DOUYIN_PACKAGE && pkg != XHS_PACKAGE) return
        // ⚠ 读不到窗口 ≠ 页面不认识。服务正在被销毁/重建、或页面正在切换时，
        // rootInActiveWindow 会短暂返回 null。把 null 当成"未知页面"会让协调器
        // 白白烧掉自动恢复次数，最后报一句「页面无法识别」——**原因是错的**，
        // 真实情况是这一刻根本没读到屏幕。
        val root = rootInActiveWindow ?: return
        // 🔴 **事件来自抖音，不代表当前窗口就是抖音。**
        // 切换 App 的那一瞬间，事件包名已经是抖音、而 rootInActiveWindow 还是上一个
        // 窗口——上一个窗口往往正是我们自己的 Agent 界面。而我们自己那句说明里
        // 有「验证码」三个字，正好命中 securityChallenge 选择器：于是任务一开始就
        // 被判成「检测到安全验证」，直接转人工、卡死到超时。
        // 实测（云机 6012，任务 #1609）截图就是我们自己的 MainActivity。
        // 所以必须按**窗口自己的包名**再过一道。
        val rootPkg = root.packageName?.toString()
        if (rootPkg != DOUYIN_PACKAGE && rootPkg != XHS_PACKAGE) return
        recognizer.anomaly(root)?.let(AgentRuntime::onAnomaly)
        val page = recognizer.recognize(root)
        if (page != currentPage || page == DouyinPage.UNKNOWN) {
            currentPage = page
            AgentRuntime.onPageChanged(page)
        }
    }

    /** Public entry so the foreground-service tick can also poll for the consent
     * dialog — the one-shot accessibility event can fire before the dialog's text
     * tree is ready (esp. EMUI), and the dialog emits no further events to retry. */
    fun confirmCaptureConsent(): Boolean = autoConfirmCaptureDialog()

    private fun autoConfirmCaptureDialog(): Boolean {
        if (!this::actions.isInitialized) return false
        val root = rootInActiveWindow ?: return false
        // Identify the screen-capture consent dialog by its capture-SPECIFIC
        // wording (varies by ROM) so we never mis-tap a generic 允许 on an
        // unrelated permission dialog. MIUI 10: "将开始截取您的屏幕上显示的所有
        // 内容。"; EMUI/Huawei: "是否允许"…"录制/投射您的屏幕".
        val capturePhrases = listOf(
            "录制/投射", "投射您的屏幕", "录制您的屏幕", "截取您的屏幕",
            "屏幕上显示的所有内容", "录屏", "capture your screen", "recording or casting",
        )
        val startLabels = listOf(
            "立即开始", "允许", "开始投射", "开始录制",
            "START NOW", "Start now", "ALLOW", "Allow",
        )
        val isCaptureDialog = capturePhrases.any { actions.hasText(root, it) } ||
            startLabels.any { root.findAccessibilityNodeInfosByText(it).isNotEmpty() &&
                actions.hasText(root, "屏幕") }
        if (!isCaptureDialog) return false
        // Tick "不再显示" first so MIUI remembers the grant and never shows the
        // dialog again — verified true no-root auto-authorization on Android 10.
        actions.clickAnyText(root, listOf("不再显示", "不再提示", "Don't show again"))
        // Re-fetch the (possibly refreshed) tree before tapping the button. EMUI
        // uses 允许; MIUI 立即开始. Tap by EXACT match — the dialog TITLE
        // ("是否允许…录制") contains "允许", so a contains-match would click the
        // title text (no-op) and the dialog times out → rejected.
        val current = rootInActiveWindow ?: root
        return startLabels.any { actions.tapExact(current, it) }
    }

    /**
     * Re-runs page recognition on demand (driven by the agent's periodic tick).
     * A static screen emits no accessibility events, so without this a missed
     * tap would never get a retry.
     */
    fun reevaluate() {
        recognizer.anomaly(rootInActiveWindow)?.let(AgentRuntime::onAnomaly)
        val page = recognizer.recognize(rootInActiveWindow)
        currentPage = page
        AgentRuntime.onPageChanged(page)
    }

    /**
     * Reads the logged-in account from a platform's profile ("我") page: the
     * account id comes from the "<idLabel>：xxx" text (抖音号 / 小红书号), the
     * nickname from the profile-name node sitting just above it.
     */
    fun readAccount(idLabel: String = "抖音号"): AccountInfo {
        val root = rootInActiveWindow ?: return AccountInfo(null, null)
        val texts = mutableListOf<Pair<String, Int>>() // text -> top y
        val queue = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            val text = node.text?.toString()?.trim()
            if (!text.isNullOrBlank()) {
                val rect = android.graphics.Rect()
                node.getBoundsInScreen(rect)
                texts.add(text to rect.top)
            }
            repeat(node.childCount) { index -> node.getChild(index)?.let(queue::add) }
        }
        val idEntry = texts.firstOrNull { it.first.contains(idLabel) }
        if (idEntry == null) {
            // No 抖音号/小红书号 on the 我 page → either logged out or the wrong
            // page. Detect logged-out by the login prompts so the matrix can show
            // "未登录" (vs a mere read miss, which stays unknown=null).
            val loginMarkers = listOf(
                "立即登录", "一键登录", "手机号登录", "登录后", "登录抖音", "登录小红书",
                "登录/注册", "新用户", "验证码登录", "其他方式登录",
            )
            val loggedOut = texts.any { t -> loginMarkers.any { t.first.contains(it) } } ||
                texts.any { it.first == "登录" }
            return AccountInfo(null, null, loggedIn = if (loggedOut) false else null)
        }
        val accountId = idEntry.first
            .replace(idLabel, "")
            .replace("：", "")
            .replace(":", "")
            .trim()
            .ifBlank { null }
        val nickname = texts
            .filter { it.second in 250 until idEntry.second }
            .filter { !it.first.contains(idLabel) && it.first.length in 1..24 }
            .maxByOrNull { it.second }
            ?.first
        return AccountInfo(nickname, accountId, loggedIn = true)
    }

    /**
     * Reads group chats from the messages list. Each conversation row's
     * contentDescription is "[置顶,]<群名>,<副标题>,…" where the 副标题 is one of:
     * 「N名群友在线…」(在线状态) / 消息预览 / 「未读N条」. So:
     *   - 群名 = the FIRST field after stripping a leading 置顶/免打扰 label
     *     (NOT "the field containing 群" — many group names have no 群, e.g.
     *     「上海买房避坑捡漏2裙」uses 裙 to dodge risk-control; the old code then
     *     wrongly picked the 「N名群友在线」status field as the name).
     *   - a row IS a group when its desc contains 「名群友」OR its name contains 群.
     *   - 私聊 (no 名群友, name has no 群) is skipped, so a person is never mistaken
     *     for a group.
     */
    fun readGroups(): List<GroupInfo> {
        val root = rootInActiveWindow ?: return emptyList()
        val result = LinkedHashMap<String, GroupInfo>()
        val labels = setOf("置顶", "免打扰", "消息")
        val queue = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            val desc = node.contentDescription?.toString()
            if (node.isClickable && !desc.isNullOrBlank()) {
                // 只按半角逗号切字段（分隔符）；全角「，」只出现在消息正文里，不能拿来切。
                val parts = desc.split(",")
                    .map { seg -> seg.filterNot { it.isSurrogate() }.trim() }
                    .filter { it.isNotEmpty() }
                // 群名 = 去掉开头「置顶/免打扰」等标签后的第一段（不再靠「含『群』」去猜）。
                val name = parts.firstOrNull { p ->
                    p !in labels && !p.endsWith("按钮") && !p.endsWith("标签")
                }
                val members =
                    Regex("(\\d+)名群友").find(desc)?.groupValues?.get(1)?.toIntOrNull()
                // 判定为群：desc 含「名群友」(在线状态行) 或 名字里带「群」。两种结构都覆盖。
                val isGroup = name != null && (desc.contains("名群友") || name.contains("群"))
                if (isGroup && name != null && name.length in 2..40 &&
                    !result.containsKey(name)
                ) {
                    result[name] = GroupInfo(name, members)
                }
            }
            repeat(node.childCount) { index -> node.getChild(index)?.let(queue::add) }
        }
        return result.values.toList()
    }

    fun captureScreenshot(callback: (Result<ScreenshotData>) -> Unit) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            callback(Result.failure(UnsupportedOperationException("截图需要 Android 11 或更高版本")))
            return
        }
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            mainExecutor,
            object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    val hardwareBuffer = screenshot.hardwareBuffer
                    try {
                        val hardwareBitmap = Bitmap.wrapHardwareBuffer(
                            hardwareBuffer,
                            screenshot.colorSpace,
                        ) ?: error("无法读取截图缓冲区")
                        val bitmap = hardwareBitmap.copy(Bitmap.Config.ARGB_8888, false)
                        val output = ByteArrayOutputStream()
                        bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
                        callback(
                            Result.success(
                                ScreenshotData(
                                    width = bitmap.width,
                                    height = bitmap.height,
                                    png = output.toByteArray(),
                                ),
                            ),
                        )
                        bitmap.recycle()
                    } catch (error: Throwable) {
                        callback(Result.failure(error))
                    } finally {
                        hardwareBuffer.close()
                    }
                }

                override fun onFailure(errorCode: Int) {
                    callback(Result.failure(IllegalStateException("截图失败，错误码 $errorCode")))
                }
            },
        )
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        AgentRuntime.onAccessibilityStopped(this)
        super.onDestroy()
    }

    data class ScreenshotData(
        val width: Int,
        val height: Int,
        val png: ByteArray,
    )

    data class AccountInfo(
        val nickname: String?,
        val douyinId: String?,
        // true = 抖音号/小红书号 read (logged in); false = a login prompt detected
        // (logged OUT); null = couldn't tell (wrong page / read failed).
        val loggedIn: Boolean? = null,
    )

    data class GroupInfo(
        val name: String,
        val memberCount: Int?,
    )

    data class PostMetricInfo(
        val title: String,
        val views: Int,
        val likes: Int,
        val collects: Int = 0,
        val comments: Int = 0,
        val body: String = "",  // 详情页正文/caption（供二改）
    )

    /**
     * Reads 小红书 per-note metrics from the profile ("我") grid: each note's
     * content-desc encodes everything: "笔记,<标题>,作者：xxx,<N>赞，<M>阅读" — so
     * one screen scrape gives title + 点赞 + 阅读 without opening each note.
     * (抖音 metrics now come from 创作者中心 → 作品分析, see readDouyinCreatorMetrics.)
     */
    fun readPostMetrics(platform: String): List<PostMetricInfo> {
        if (platform != "xhs") return emptyList()
        val root = rootInActiveWindow ?: return emptyList()
        val result = LinkedHashMap<String, PostMetricInfo>()
        val queue = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            val desc = node.contentDescription?.toString()
            // 小红书 我-grid: "笔记,<标题>,来自<昵称>,<N>赞，<M>阅读" (older builds
            // used ",作者：…" — strip whichever appears).
            if (desc != null && desc.startsWith("笔记,")) {
                val title = desc.removePrefix("笔记,")
                    .substringBefore(",来自")
                    .substringBefore(",作者")
                    .trim()
                // ⚠ 必须走 parseCount：小红书破万会显示「1.2万赞」，而 `(\d+)赞` 只
                // 匹配纯数字 —— 破万的笔记会被读成 0，越爆的笔记数据越假，而且这个 0
                // 会作为一次"计数变化"写进快照时间线，把增量算成大额负数再算成等额正数。
                val likes = parseCount(
                    Regex("([0-9.]+万?)赞").find(desc)?.groupValues?.get(1)
                )
                val views = parseCount(
                    Regex("([0-9.]+万?)阅读").find(desc)?.groupValues?.get(1)
                )
                if (title.isNotBlank()) result[title] = PostMetricInfo(title, views, likes)
            }
            repeat(node.childCount) { index -> node.getChild(index)?.let(queue::add) }
        }
        return result.values.toList()
    }

    /**
     * 小红书 笔记详情页 bottom bar: 点赞/收藏/评论 are content-desc "<label> <count>"
     * and 浏览 is a text node "<N>浏览" near the date. The grid only exposes 阅读+赞,
     * so opening each note here is the only way to also get 收藏 + 评论.
     */
    fun readXhsNoteDetail(): PostMetricInfo? {
        val root = rootInActiveWindow ?: return null
        var views = 0
        var likes = 0
        var collects = 0
        var comments = 0
        var found = false
        val texts = mutableListOf<String>()
        val queue = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            node.contentDescription?.toString()?.let { d ->
                Regex("点赞\\s*([0-9.]+万?)").find(d)?.let { likes = parseCount(it.groupValues[1]); found = true }
                Regex("收藏\\s*([0-9.]+万?)").find(d)?.let { collects = parseCount(it.groupValues[1]); found = true }
                Regex("评论\\s*([0-9.]+万?)").find(d)?.let { comments = parseCount(it.groupValues[1]); found = true }
            }
            node.text?.toString()?.let { t ->
                Regex("([0-9.]+万?)\\s*浏览").find(t)?.let { views = parseCount(it.groupValues[1]) }
                if (t.isNotBlank()) texts.add(t.trim())
            }
            repeat(node.childCount) { index -> node.getChild(index)?.let(queue::add) }
        }
        // 正文 and #话题 are SEPARATE text nodes. 正文 = the longest PROSE node
        // (not a #hashtag block, not chrome); 话题 = the #tags from any node. Store
        // 正文 + 话题 together so the 二改 engine sees both. (TextView holds the full
        // text even when visually truncated, so no scroll/展开 is needed.)
        val topics = texts
            .flatMap { Regex("#([^#\\s]+)").findAll(it).map { m -> m.groupValues[1] }.toList() }
            .distinct()
        val prose = texts
            .filter { t ->
                !t.startsWith("#") &&
                    t.count { it == '#' } < 2 &&
                    t.length >= 4 &&
                    metricStop.none { s -> t.contains(s) }
            }
            .maxByOrNull { it.length }
            .orEmpty()
        val body = buildString {
            append(prose)
            if (topics.isNotEmpty()) {
                if (prose.isNotEmpty()) append("\n")
                append(topics.joinToString(" ") { "#$it" })
            }
        }
        return if (found) PostMetricInfo("", views, likes, collects, comments, body) else null
    }

    private val metricStop = listOf(
        "浏览", "点赞", "收藏", "评论", "分享", "说点什么", "关注", "编辑", "回复",
    )

    /**
     * 抖音 创作者中心 → 数据分析 → 作品分析: the page lists each work as a contiguous
     * run of text nodes (document order):
     *   <full 正文 + #话题>           ← 标题
     *   "2026-06-25 17:58 发布"       ← 发布时间 (boundary marker)
     *   播放 / <n>  点赞 / <n>  评论 / <n>  收藏 / <n>
     * Read in DFS pre-order so each title precedes its metrics. Far more robust than
     * the 我-grid: real text nodes (no image covers), full 正文 for content matching,
     * no opening each work, and the page is fully loaded (no splash/tree race).
     */
    fun readDouyinCreatorMetrics(): List<PostMetricInfo> {
        val root = rootInActiveWindow ?: return emptyList()
        val texts = ArrayList<String>()
        fun dfs(node: android.view.accessibility.AccessibilityNodeInfo?) {
            node ?: return
            node.text?.toString()?.trim()?.let { if (it.isNotBlank()) texts.add(it) }
            for (i in 0 until node.childCount) dfs(node.getChild(i))
        }
        dfs(root)
        val dateRx = Regex("\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}")
        val result = LinkedHashMap<String, PostMetricInfo>()
        var i = 0
        while (i < texts.size) {
            if (dateRx.containsMatchIn(texts[i])) {
                // title = nearest preceding long Chinese text (skip "1 天" age label)
                var title = ""
                var k = i - 1
                while (k >= 0 && k >= i - 4) {
                    val t = texts[k]
                    if (t.length >= 6 && t.any { it in '一'..'鿿' }) { title = t; break }
                    k--
                }
                var views = 0; var likes = 0; var comments = 0; var collects = 0
                var j = i + 1
                while (j < texts.size && j < i + 16 && !dateRx.containsMatchIn(texts[j])) {
                    when (texts[j]) {
                        "播放" -> views = parseCount(texts.getOrNull(j + 1))
                        "点赞" -> likes = parseCount(texts.getOrNull(j + 1))
                        "评论" -> comments = parseCount(texts.getOrNull(j + 1))
                        "收藏" -> collects = parseCount(texts.getOrNull(j + 1))
                    }
                    j++
                }
                if (title.length >= 6) {
                    result[title] = PostMetricInfo(title, views, likes, collects, comments, title)
                }
                i = j
            } else {
                i++
            }
        }
        if (result.isEmpty()) {
            val sample = texts.filter { it.length in 2..16 }.distinct().take(14)
            android.util.Log.i("DYMetrics", "creator EMPTY page sample=$sample")
        }
        android.util.Log.i("DYMetrics", "creator parsed ${result.size} works")
        return result.values.toList()
    }

    /**
     * Reads the author's pinned first comment from an opened 抖音 comment panel —
     * on 图文 Notes this comment holds the full 正文+话题 (the cover is an image).
     * Our pinned comment is the only one carrying #话题, so prefer the longest
     * text node containing '#'; else the longest non-chrome prose node.
     */
    fun readFirstComment(): String? {
        val root = rootInActiveWindow ?: return null
        data class T(val text: String, val cy: Int)
        val texts = mutableListOf<T>()
        val queue = ArrayDeque<android.view.accessibility.AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            val t = node.text?.toString()?.trim()
            if (!t.isNullOrBlank()) {
                val r = android.graphics.Rect()
                node.getBoundsInScreen(r)
                texts.add(T(t, r.centerY()))
            }
            repeat(node.childCount) { index -> node.getChild(index)?.let(queue::add) }
        }
        // ONLY the author's pinned comment carries #话题 (fan replies don't), so the
        // #tags are a fan-proof signal. Return just the 话题 line — the caller
        // appends it to OUR 大字报 caption (which is our 正文, also fan-proof).
        val topics = texts
            .flatMap { Regex("#([^#\\s]+)").findAll(it.text).map { m -> m.groupValues[1] }.toList() }
            .distinct()
        if (topics.isEmpty()) return null
        return topics.joinToString(" ") { "#$it" }
    }

    /** "1.2万" -> 12000, "2402" -> 2402. */
    private fun parseCount(raw: String?): Int {
        if (raw.isNullOrBlank()) return 0
        return if (raw.contains("万")) {
            (raw.removeSuffix("万").toDoubleOrNull()?.times(10000))?.toInt() ?: 0
        } else {
            raw.toDoubleOrNull()?.toInt() ?: 0
        }
    }

    companion object {
        const val DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
        const val XHS_PACKAGE = "com.xingin.xhs"
    }
}
