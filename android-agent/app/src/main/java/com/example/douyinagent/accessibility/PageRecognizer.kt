package com.example.douyinagent.accessibility

import android.view.accessibility.AccessibilityNodeInfo
import com.example.douyinagent.execution.DouyinPage

/** 判成「安全验证」时到底命中了屏幕上的哪句话。
 *
 * 以前只报一句「检测到安全验证，请人工处理」，运营（和我）都无从判断这是抖音
 * 真的弹了验证，还是我们自己的选择器认错了 —— 而认错的代价是任务当场转人工、
 * 一直卡到超时。把命中的原文带出来，这个判断一秒就能做完。 */
object RecognitionHint {
    @Volatile
    var securityMatch: String? = null
}

class PageRecognizer {
    fun recognize(root: AccessibilityNodeInfo?): DouyinPage {
        if (root == null) return DouyinPage.UNKNOWN
        val texts = collectTexts(root)
        val s = Selectors.current

        // The bottom navigation bar (home / friends / messages / me) carries a
        // center "拍摄按钮" content-desc, so it appears on every main tab. It must
        // be matched as HOME *before* any "拍摄"-based publish-entry heuristic,
        // otherwise every tabbed screen is mistaken for the publish entry.
        val hasMainTabBar = s.homeTabsPrimary.all { containsAny(texts, it) } &&
            containsAny(texts, *s.homeTabsSecondary.toTypedArray())

        val securityHit = texts.firstOrNull { t -> s.securityChallenge.any { t.contains(it) } }
        return when {
            securityHit != null -> {
                RecognitionHint.securityMatch = securityHit.take(60)
                DouyinPage.SECURITY_CHALLENGE
            }
            // Final publish page: title field + the "发作品" publish button. Must be
            // checked before PUBLISH_SUCCESS — the publish page contains the hint
            // "发布成功后将保存内容至本地", which would falsely match success.
            containsAny(texts, *s.publishButtons.toTypedArray()) &&
                containsAny(texts, *s.publishTitleHints.toTypedArray()) ->
                DouyinPage.PUBLISH_CONFIRM
            containsAny(texts, *s.publishSuccess.toTypedArray()) ->
                DouyinPage.PUBLISH_SUCCESS
            // Text-card editor: the template strip ("文字模版"/"换一换") OR the
            // decorate page (贴纸/滤镜/特效/标记) — both sit before the publish
            // page and just need "下一步".
            containsAny(texts, *s.templateNext.toTypedArray()) &&
                containsAny(texts, *s.templateMarkers.toTypedArray()) ->
                DouyinPage.TEXT_TEMPLATE
            // Text composer: "写文字 / 写长文" tabs and the body input hint.
            containsAny(texts, *s.composerHints.toTypedArray()) ||
                (containsAny(texts, *s.composerWrite.toTypedArray()) &&
                    containsAny(texts, *s.templateNext.toTypedArray())) ->
                DouyinPage.TEXT_COMPOSER
            containsAny(texts, *s.mediaPicker.toTypedArray()) ->
                DouyinPage.MEDIA_PICKER
            // Camera/record page: bottom mode tabs include 文字 and 相机.
            containsAny(texts, *s.publishEntryText.toTypedArray()) &&
                containsAny(texts, *s.publishEntryMarkers.toTypedArray()) ->
                DouyinPage.PUBLISH_ENTRY
            hasMainTabBar ->
                DouyinPage.HOME
            else -> DouyinPage.UNKNOWN
        }
    }

    /**
     * Detects account-anomaly warnings that Douyin shows on the messages page or
     * during publishing. Returns a short reason, or null when the screen is fine.
     * Phrases are specific system warnings to avoid matching ordinary content.
     */
    fun anomaly(root: AccessibilityNodeInfo?): String? {
        if (root == null) return null
        val texts = collectTexts(root)
        val s = Selectors.current
        return when {
            containsAny(texts, *s.anomalyReview.toTypedArray()) ->
                "内容审核未通过/涉嫌违规"
            containsAny(texts, *s.anomalyRateLimited.toTypedArray()) ->
                "账号被限流"
            containsAny(texts, *s.anomalyTooFrequent.toTypedArray()) ->
                "操作过于频繁，需暂停"
            containsAny(texts, *s.anomalyBanned.toTypedArray()) ->
                "账号异常/被封禁"
            else -> null
        }
    }

    private fun containsAny(texts: List<String>, vararg candidates: String): Boolean =
        texts.any { text -> candidates.any(text::contains) }

    private fun collectTexts(root: AccessibilityNodeInfo): List<String> {
        val result = mutableListOf<String>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            node.text?.toString()?.takeIf(String::isNotBlank)?.let(result::add)
            node.contentDescription?.toString()
                ?.takeIf(String::isNotBlank)
                ?.let(result::add)
            node.hintText?.toString()?.takeIf(String::isNotBlank)?.let(result::add)
            repeat(node.childCount) { index ->
                node.getChild(index)?.let(queue::add)
            }
        }
        return result.distinct()
    }
}
