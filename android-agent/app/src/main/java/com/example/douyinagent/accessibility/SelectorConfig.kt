package com.example.douyinagent.accessibility

import org.json.JSONObject

/**
 * All the Douyin-UI-dependent text phrases in one place, so a Douyin redesign
 * (renamed buttons / changed wording) can be patched by editing the backend's
 * selectors.json — no APK rebuild. The built-in [DEFAULT] is the source of truth
 * until/unless the backend serves an override (see AgentApiClient.fetchSelectors).
 */
data class SelectorConfig(
    val securityChallenge: List<String>,
    val publishButtons: List<String>,
    val publishTitleHints: List<String>,
    val publishSuccess: List<String>,
    val templateNext: List<String>,
    val templateMarkers: List<String>,
    val composerHints: List<String>,
    val composerWrite: List<String>,
    val mediaPicker: List<String>,
    val publishEntryText: List<String>,
    val publishEntryMarkers: List<String>,
    val homeTabsPrimary: List<String>,
    val homeTabsSecondary: List<String>,
    val shootButton: List<String>,
    val bodyFieldHints: List<String>,
    val anomalyReview: List<String>,
    val anomalyRateLimited: List<String>,
    val anomalyTooFrequent: List<String>,
    val anomalyBanned: List<String>,
) {
    companion object {
        val DEFAULT = SelectorConfig(
            securityChallenge = listOf("安全验证", "验证码", "人脸验证", "滑块验证"),
            publishButtons = listOf("发作品", "发布日常", "发布"),
            publishTitleHints = listOf("添加标题", "添加作品标题", "作品标题", "标题", "title"),
            publishSuccess = listOf("作品发布成功", "发布成功！"),
            templateNext = listOf("下一步"),
            templateMarkers = listOf(
                "文字模版", "文字模板", "换一换", "贴纸", "滤镜", "特效", "标记",
            ),
            composerHints = listOf("分享你的想法", "写长文", "输入文字", "正文"),
            composerWrite = listOf("写文字"),
            mediaPicker = listOf("最近项目", "所有照片", "相机胶卷"),
            publishEntryText = listOf("文字"),
            publishEntryMarkers = listOf("相机", "开直播", "创作灵感"),
            homeTabsPrimary = listOf("首页", "我"),
            // ⚠ 实测（云机 6019，抖音 39.0.0）底部那颗「+」的 content-desc 是
            // **「拍摄，按钮」带一个中文逗号**，不是「拍摄按钮」。contains 匹配下
            // 后者永远命中不了，一直靠 shootButton 里那个宽松的「拍摄」兜着 ——
            // 而「拍摄」在别的页面也会出现，宽松匹配迟早点错地方。
            homeTabsSecondary = listOf("消息", "朋友", "拍摄，按钮", "拍摄按钮"),
            shootButton = listOf("拍摄，按钮", "拍摄按钮", "拍摄", "发布"),
            bodyFieldHints = listOf("分享你的想法", "写文字", "输入文字", "正文"),
            anomalyReview = listOf(
                "审核未通过", "审核不通过", "内容涉嫌违规", "未通过审核", "违反社区规范",
            ),
            anomalyRateLimited = listOf("已被限流", "流量被限制", "限制了部分流量", "作品被限流"),
            anomalyTooFrequent = listOf("操作过于频繁", "操作太频繁", "发布过于频繁"),
            anomalyBanned = listOf("账号存在异常", "账号被封", "永久封禁", "账号已被封禁", "封禁"),
        )

        // 小红书 (com.xingin.xhs) 写文字/大字报 flow — same structure as Douyin,
        // mapped live on the MI 9 (see xhs-publish-flow memory). Entry differs:
        // HOME → nav「发布」→ sheet「写文字」→ composer → preview → 发布笔记.
        val DEFAULT_XHS = DEFAULT.copy(
            publishButtons = listOf("发布笔记", "发布"),
            publishTitleHints = listOf("添加标题"),
            publishSuccess = listOf("发布成功", "笔记发布成功", "发布成功！"),
            templateNext = listOf("下一步"),
            templateMarkers = listOf("预览", "换搭配", "基础", "美漫", "插图", "涂写", "光影"),
            composerHints = listOf("写想法", "说点什么或提个问题", "展开说说"),
            composerWrite = listOf("写文字"),
            publishEntryText = listOf("写文字"),
            publishEntryMarkers = listOf("从相册选择", "写文字", "相机"),
            homeTabsPrimary = listOf("首页", "我"),
            homeTabsSecondary = listOf("市集", "消息", "发布"),
            shootButton = listOf("发布"),
            bodyFieldHints = listOf("写想法", "说点什么", "展开说说", "正文"),
        )

        /** Parse a backend JSON override; any missing key falls back to [DEFAULT]. */
        fun fromJson(json: JSONObject): SelectorConfig {
            fun arr(key: String, fallback: List<String>): List<String> {
                val a = json.optJSONArray(key) ?: return fallback
                return (0 until a.length()).map { a.getString(it) }
            }
            return SelectorConfig(
                securityChallenge = arr("security_challenge", DEFAULT.securityChallenge),
                publishButtons = arr("publish_buttons", DEFAULT.publishButtons),
                publishTitleHints = arr("publish_title_hints", DEFAULT.publishTitleHints),
                publishSuccess = arr("publish_success", DEFAULT.publishSuccess),
                templateNext = arr("template_next", DEFAULT.templateNext),
                templateMarkers = arr("template_markers", DEFAULT.templateMarkers),
                composerHints = arr("composer_hints", DEFAULT.composerHints),
                composerWrite = arr("composer_write", DEFAULT.composerWrite),
                mediaPicker = arr("media_picker", DEFAULT.mediaPicker),
                publishEntryText = arr("publish_entry_text", DEFAULT.publishEntryText),
                publishEntryMarkers = arr("publish_entry_markers", DEFAULT.publishEntryMarkers),
                homeTabsPrimary = arr("home_tabs_primary", DEFAULT.homeTabsPrimary),
                homeTabsSecondary = arr("home_tabs_secondary", DEFAULT.homeTabsSecondary),
                shootButton = arr("shoot_button", DEFAULT.shootButton),
                bodyFieldHints = arr("body_field_hints", DEFAULT.bodyFieldHints),
                anomalyReview = arr("anomaly_review", DEFAULT.anomalyReview),
                anomalyRateLimited = arr("anomaly_rate_limited", DEFAULT.anomalyRateLimited),
                anomalyTooFrequent = arr("anomaly_too_frequent", DEFAULT.anomalyTooFrequent),
                anomalyBanned = arr("anomaly_banned", DEFAULT.anomalyBanned),
            )
        }
    }
}

/**
 * Process-wide selector sets. `douyin` may be overridden by the backend; `xhs`
 * uses the built-in map. `current` points at whichever platform the in-flight
 * task uses (set by TaskCoordinator.start), and is what the recognizer + flow
 * read. Defaults to douyin when idle.
 */
object Selectors {
    @Volatile
    var douyin: SelectorConfig = SelectorConfig.DEFAULT
    val xhs: SelectorConfig = SelectorConfig.DEFAULT_XHS

    @Volatile
    var current: SelectorConfig = SelectorConfig.DEFAULT

    fun forPlatform(platform: String): SelectorConfig =
        if (platform == "xhs") xhs else douyin

    fun usePlatform(platform: String) {
        current = forPlatform(platform)
    }
}
