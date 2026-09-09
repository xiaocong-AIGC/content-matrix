package com.example.douyinagent.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.util.Base64
import android.view.accessibility.AccessibilityNodeInfo

class AccessibilityActions(
    private val service: AccessibilityService,
) {
    fun clickAnyText(root: AccessibilityNodeInfo?, candidates: List<String>): Boolean {
        for (candidate in candidates) {
            val nodes = root?.findAccessibilityNodeInfosByText(candidate).orEmpty()
            val exact = nodes.firstOrNull {
                it.text?.toString() == candidate ||
                    it.contentDescription?.toString() == candidate
            }
            if (exact != null && clickNodeOrParent(exact)) return true
            if (nodes.firstOrNull()?.let(::clickNodeOrParent) == true) return true
        }
        // Fallback: findAccessibilityNodeInfosByText misses some content-desc-only
        // controls (e.g. the bottom "拍摄按钮"), so walk the tree and match on a
        // substring of either text or content description.
        val all = collectNodes(root)
        for (candidate in candidates) {
            val match = all.firstOrNull { node ->
                node.text?.toString()?.contains(candidate) == true ||
                    node.contentDescription?.toString()?.contains(candidate) == true
            }
            if (match != null && clickNodeOrParent(match)) return true
        }
        return false
    }

    /**
     * Taps the on-screen center of the first node whose text or content
     * description contains a candidate. Douyin's bottom bar (the center shoot
     * button, etc.) is custom-drawn and not clickable via ACTION_CLICK, so a
     * dispatched gesture at the node's real coordinates is the reliable path.
     */
    fun tapByText(root: AccessibilityNodeInfo?, candidates: List<String>): Boolean {
        val all = collectNodes(root)
        for (candidate in candidates) {
            val match = all.firstOrNull { node ->
                node.text?.toString()?.contains(candidate) == true ||
                    node.contentDescription?.toString()?.contains(candidate) == true
            } ?: continue
            val rect = Rect()
            match.getBoundsInScreen(rect)
            if (rect.width() > 0 && rect.height() > 0 &&
                tap(rect.exactCenterX(), rect.exactCenterY())
            ) {
                return true
            }
        }
        return false
    }

    /** All visible node texts/descriptions containing `substr` — for debugging
     * "why didn't we find the group" by logging what the agent actually sees. */
    fun textsMatching(root: AccessibilityNodeInfo?, substr: String): List<String> {
        return collectNodes(root)
            .mapNotNull { it.text?.toString() ?: it.contentDescription?.toString() }
            .filter { it.contains(substr) }
            .distinct()
    }

    /** Tap an absolute screen point (for grid cells with no accessibility text). */
    fun tapPoint(x: Float, y: Float): Boolean = tap(x, y)

    /** The live root, for re-reading the tree from a background worker. */
    fun root(): AccessibilityNodeInfo? = service.rootInActiveWindow

    private fun findBodyEditable(root: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        val editables = collectNodes(root).filter { it.isEditable }
        return editables.firstOrNull { node ->
            val id = listOfNotNull(
                node.hintText?.toString(), node.text?.toString(),
                node.contentDescription?.toString(), node.viewIdResourceName,
            ).joinToString(" ")
            !id.contains("标题") && !id.contains("title", ignoreCase = true)
        } ?: editables.lastOrNull()
    }

    /**
     * Tap the body (non-title) editable to RAISE THE KEYBOARD (so an IME's
     * commitText has a live input connection), then move the cursor to the END
     * so injected 话题 tags append after the body. Needed before the orchestrator
     * types topics via our in-app IME.
     */
    fun focusBodyEnd(): Boolean {
        val body0 = findBodyEditable(service.rootInActiveWindow) ?: return false
        val rect = Rect()
        body0.getBoundsInScreen(rect)
        if (rect.width() > 0 && rect.height() > 0) {
            tap(rect.exactCenterX(), rect.exactCenterY())
        }
        sleepQuiet(800)
        val body = findBodyEditable(service.rootInActiveWindow) ?: return true
        val len = body.text?.length ?: 0
        val args = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, len)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, len)
        }
        body.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)
        return true
    }

    /** Tap the on-screen center of the first node whose content-desc matches [re]
     * (e.g. the 抖音 "评论N，按钮" action-bar icon). */
    fun tapByDescRegex(root: AccessibilityNodeInfo?, re: Regex): Boolean {
        // A vertical pager pre-renders neighbor pages whose matching nodes have
        // zero/off-screen bounds — pick the first match that is actually on-screen.
        val h = service.resources.displayMetrics.heightPixels
        for (node in collectNodes(root)) {
            val desc = node.contentDescription?.toString() ?: continue
            if (!re.containsMatchIn(desc)) continue
            val rect = Rect()
            node.getBoundsInScreen(rect)
            if (rect.width() > 0 && rect.height() > 0 && rect.centerY() in 0..h) {
                return tap(rect.exactCenterX(), rect.exactCenterY())
            }
        }
        return false
    }

    /**
     * Taps the on-screen center of the node whose text OR content description
     * EXACTLY equals [label]. Use for bottom-nav buttons like 我 — a "contains"
     * match (tapByText) would hit a feed note whose body merely contains the word.
     */
    fun tapExact(root: AccessibilityNodeInfo?, label: String): Boolean {
        val match = collectNodes(root).firstOrNull { node ->
            node.text?.toString() == label || node.contentDescription?.toString() == label
        } ?: return false
        val rect = Rect()
        match.getBoundsInScreen(rect)
        return rect.width() > 0 && rect.height() > 0 && tap(rect.exactCenterX(), rect.exactCenterY())
    }

    /**
     * Tap a bottom-nav tab (我 / 消息 …) SAFELY. A contains-match (tapByText) hits
     * feed captions / comments / nicknames that merely contain the word → random
     * taps ("乱点") on the live app. Match the EXACT tab label, then its button
     * content-desc ("我，按钮" / "消息，按钮"), across ROM/app-version variants.
     */
    fun tapBottomTab(label: String): Boolean {
        val root = service.rootInActiveWindow
        if (tapExact(root, label)) return true
        val esc = Regex.escape(label)
        return tapByDescRegex(root, Regex("^$esc[，,]?\\s*(按钮|标签).*$")) ||
            tapByDescRegex(root, Regex("^$esc$"))
    }

    /** Tap the bottom-nav 「我」 tab safely. */
    fun tapMeTab(): Boolean = tapBottomTab("我")

    /**
     * Like [clickAnyText] but searches every window (the @-mention picker is an
     * overlay window not present in rootInActiveWindow).
     */
    fun clickTextAnyWindow(candidates: List<String>): Boolean {
        for (window in service.windows) {
            val root = window.root ?: continue
            if (clickAnyText(root, candidates)) return true
        }
        return false
    }

    /**
     * Same as [textsMatching] but across every window, not just the active one.
     * Right after a page transition `rootInActiveWindow` can still be the previous
     * window (or null), so a check that only looks there reports "not there yet"
     * for a page that is plainly on screen.
     */
    fun textsMatchingAnyWindow(substr: String): List<String> {
        val out = mutableListOf<String>()
        for (window in service.windows) {
            val root = window.root ?: continue
            out += textsMatching(root, substr)
        }
        return out.distinct()
    }

    /**
     * True when ONE window contains every string in [allOf] and at least one of
     * [anyOf] (substring match over text + content-desc).
     *
     * 「同一个窗口」是重点：会话打开后，消息列表那个窗口仍然留在 `service.windows`
     * 里。跨窗口拼凑会把「列表里有群名」+「某个私聊里有输入框」凑成"我在目标群里"，
     * 正好是我们要防的那种误判。
     */
    fun windowContains(allOf: List<String>, anyOf: List<String>): Boolean {
        for (window in service.windows) {
            val root = window.root ?: continue
            val texts = collectNodes(root).mapNotNull { node ->
                // text 为空串时要退回 content-desc：抖音的会话行只有 desc。
                node.text?.toString()?.ifBlank { null }
                    ?: node.contentDescription?.toString()?.ifBlank { null }
            }
            val hasAll = allOf.all { needle -> texts.any { it.contains(needle) } }
            val hasAny = anyOf.isEmpty() || anyOf.any { needle -> texts.any { it.contains(needle) } }
            if (hasAll && hasAny) return true
        }
        return false
    }

    /** Every visible string, window by window — for logging "what did it actually see". */
    fun windowTexts(limit: Int = 10): List<String> {
        val out = mutableListOf<String>()
        for (window in service.windows) {
            val root = window.root ?: continue
            out += collectNodes(root).mapNotNull { node ->
                node.text?.toString()?.ifBlank { null }
                    ?: node.contentDescription?.toString()?.ifBlank { null }
            }
        }
        return out.distinct().take(limit)
    }

    /** Tap by a fraction of the screen size (resolution-independent). */
    fun tapFraction(fx: Float, fy: Float): Boolean {
        val m = service.resources.displayMetrics
        return tap(m.widthPixels * fx, m.heightPixels * fy)
    }

    private fun tap(x: Float, y: Float): Boolean {
        // A zero-length path is dropped as an invalid gesture on many devices,
        // so move a single pixel and hold ~90ms to emulate a real tap.
        val path = Path().apply {
            moveTo(x, y)
            lineTo(x + 1f, y + 1f)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, 90L))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    /** True if any node's text or content description contains [needle]. */
    fun hasText(root: AccessibilityNodeInfo?, needle: String): Boolean {
        if (needle.isBlank()) return false
        return collectNodes(root).any { node ->
            node.text?.toString()?.contains(needle) == true ||
                node.contentDescription?.toString()?.contains(needle) == true
        }
    }

    fun fillField(
        root: AccessibilityNodeInfo?,
        hints: List<String>,
        value: String,
    ): Boolean {
        if (value.isBlank()) return true
        val editables = collectNodes(root).filter { it.isEditable }
        val matched = editables.firstOrNull { node ->
            val identity = listOfNotNull(
                node.hintText?.toString(),
                node.text?.toString(),
                node.contentDescription?.toString(),
                node.viewIdResourceName,
            ).joinToString(" ")
            hints.any { identity.contains(it, ignoreCase = true) }
        }
        return setText(matched ?: editables.firstOrNull(), value)
    }

    /**
     * Fills the body/caption editable — the editable field that is NOT the
     * title (its hint/text/id does not mention 标题/title). On the publish page
     * this is where the post caption and topics go.
     */
    fun fillBodyField(root: AccessibilityNodeInfo?, value: String): Boolean {
        if (value.isBlank()) return true
        val editables = collectNodes(root).filter { it.isEditable }
        val target = editables.firstOrNull { node ->
            val identity = listOfNotNull(
                node.hintText?.toString(),
                node.text?.toString(),
                node.contentDescription?.toString(),
                node.viewIdResourceName,
            ).joinToString(" ")
            !identity.contains("标题") && !identity.contains("title", ignoreCase = true)
        } ?: editables.lastOrNull()
        return setText(target, value)
    }

    /** Current text of the first editable field (e.g. after a mention chip). */
    fun currentEditableText(root: AccessibilityNodeInfo?): String? {
        return collectNodes(root).firstOrNull { it.isEditable }?.text?.toString()
    }

    /**
     * Put the cursor at position 0 of the editable matching `hints` (else the first
     * editable). Used before injecting a REAL "@" keystroke so 抖音's @-member picker
     * inserts the mention BEFORE the already-typed body → "@所有人 <body>".
     */
    fun setSelectionStart(root: AccessibilityNodeInfo?, hints: List<String>): Boolean {
        val editables = collectNodes(root).filter { it.isEditable }
        val node = editables.firstOrNull { n ->
            val identity = listOfNotNull(
                n.hintText?.toString(),
                n.text?.toString(),
                n.contentDescription?.toString(),
                n.viewIdResourceName,
            ).joinToString(" ")
            hints.any { identity.contains(it, ignoreCase = true) }
        } ?: editables.firstOrNull() ?: return false
        val args = android.os.Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 0)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, 0)
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)
    }

    /**
     * Focus an editor, then type. Some editors (e.g. XHS 写文字 card) lazily
     * create their EditText only AFTER a tap — it doesn't exist in the tree until
     * focused. So: tap a hint (or the card center), wait for the field to appear,
     * re-read the LIVE tree, and set the text on the focused/first editable.
     * Works for Douyin too (its field is already editable; the tap just focuses).
     */
    fun focusAndFill(tapHints: List<String>, value: String): Boolean {
        if (value.isBlank()) return true
        val root = service.rootInActiveWindow
        if (!tapByText(root, tapHints)) tapFraction(0.5f, 0.32f)
        try {
            Thread.sleep(900)
        } catch (_: InterruptedException) {
        }
        val fresh = service.rootInActiveWindow
        val editables = collectNodes(fresh).filter { it.isEditable }
        val target = editables.firstOrNull { it.isFocused } ?: editables.firstOrNull()
        return setText(target, value)
    }

    /**
     * 小红书 真·话题蓝字: for each topic, tap the 话题 toolbar button → type the
     * keyword into the focused topic-search field → tap the matching suggestion
     * row, so XHS inserts a real (blue, traffic-pool-linked) #tag. Plain "#text"
     * in the body never becomes a tag. Body must be filled BEFORE this (tags are
     * appended at the cursor; re-setting the body text would wipe them).
     */
    fun addXhsTopics(topicButtonLabels: List<String>, topics: List<String>) {
        for (raw in topics) {
            val kw = raw.trim().removePrefix("#").trim()
            if (kw.isBlank()) continue
            val root = service.rootInActiveWindow ?: continue
            // Open the topic search (exact "话题" button, not a "#话题" suggestion).
            if (!tapExact(root, "话题") && !tapByText(root, topicButtonLabels)) continue
            sleepQuiet(900)
            // Type the keyword into the now-focused search field.
            val fresh = service.rootInActiveWindow
            val search = collectNodes(fresh).firstOrNull { it.isEditable && it.isFocused }
                ?: collectNodes(fresh).filter { it.isEditable }.lastOrNull()
            if (!setText(search, kw)) {
                back() // bail out of the search if we couldn't type
                continue
            }
            sleepQuiet(1300) // let suggestions load
            tapTopicSuggestion(service.rootInActiveWindow, kw)
            sleepQuiet(700)
        }
    }

    /**
     * Insert REAL (blue, traffic-pool-linked) 小红书 话题 tags into the 正文.
     *
     * The PC orchestrator has already switched the active IME to our in-app
     * [AgentInputMethodService]; the AGENT then drives the loop (it broadcasts to
     * the same-app IME directly and finds the suggestion via the live a11y tree —
     * no flaky `uiautomator dump`). Per topic:
     *   1. cursor to the end of 正文,
     *   2. snapshot the body length,
     *   3. commitText "#kw" (FINAL text — NO composing region, so XHS's tag
     *      insertion can't desync the IME like setComposingText did),
     *   4. find + tap the matching suggestion (XHS appends the real blue tag),
     *   5. delete the typed "#kw" leftover by its exact char range via ACTION_CUT
     *      (deterministic offsets — unlike the composing/extracted-text offsets
     *      that scrambled multi-topic runs).
     * If no suggestion surfaces, the plain "#kw" simply stays (degraded fallback).
     */
    fun typeXhsTopics(topics: List<String>) {
        for (raw in topics) {
            val kw = raw.trim().removePrefix("#").trim()
            if (kw.isBlank()) continue
            // Before EVERY topic, tap the empty area at the bottom of the 正文 box
            // (it's much taller than the text) so the caret reliably lands at the
            // text END — never mid-body — and the input connection is freshly bound.
            // (Tapping the center, or relying on ACTION_SET_SELECTION alone, put the
            // caret mid-text on XHS's editor and scrambled the insert.)
            tapBodyEnd()
            sleepQuiet(450)
            val token = "#$kw"
            val before = bodyTextLength()
            // Commit "#" FIRST so XHS enters topic-search mode (it triggers on the
            // "#" event), THEN the keyword so it filters to a matching suggestion.
            // Committing "#kw" in one shot races mode-entry vs. filter and often
            // leaves XHS showing only generic recommendations.
            commitText("#")
            sleepQuiet(650)
            commitText(kw)
            sleepQuiet(1500) // let the suggestion panel populate
            var tapped = false
            for (attempt in 0 until 6) {
                if (tapTopicSuggestionAnyWindow(kw)) {
                    tapped = true
                    break
                }
                sleepQuiet(700)
            }
            if (tapped) {
                sleepQuiet(1100) // let XHS insert the tag
                val cut = deleteBodyRange(before, before + token.length)
                android.util.Log.i("AgentTopics", "tag #$kw cut=$cut")
            } else {
                android.util.Log.i("AgentTopics", "plain #$kw (no suggestion)")
            }
            sleepQuiet(700)
        }
        // The last CUT leaves a text selection (and the 全选/剪切 toolbar) on the
        // final tag — collapse the cursor to the end so the page is clean for
        // review/publish.
        findBodyEditable(service.rootInActiveWindow)?.let { body ->
            val len = body.text?.length ?: 0
            val args = Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, len)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, len)
            }
            body.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)
        }
    }

    /** Length of the 正文 editable's current text (char count). */
    private fun bodyTextLength(): Int =
        findBodyEditable(service.rootInActiveWindow)?.text?.length ?: 0

    /** Tap the on-screen END of the 正文 text so the cursor lands at the end and the
     * IME input connection is (re)bound; then snap selection to the exact end. */
    private fun tapBodyEnd() {
        val body = findBodyEditable(service.rootInActiveWindow) ?: return
        val rect = Rect()
        body.getBoundsInScreen(rect)
        if (rect.width() > 0 && rect.height() > 0) {
            // Tap just inside the bottom-right — past the last glyph, an empty spot
            // that places the caret at the text end without hitting a tag span.
            tap(rect.right - 12f, rect.bottom - 14f)
        }
        sleepQuiet(700)
        val fresh = findBodyEditable(service.rootInActiveWindow) ?: return
        val len = fresh.text?.length ?: 0
        val args = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, len)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, len)
        }
        fresh.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)
    }

    /** Select [start,end) on the 正文 and cut it — used to delete the typed "#kw"
     * leftover after XHS inserts the real tag. Bounds-checked against live text. */
    private fun deleteBodyRange(start: Int, end: Int): Boolean {
        val body = findBodyEditable(service.rootInActiveWindow) ?: return false
        val len = body.text?.length ?: return false
        if (start < 0 || end > len || start >= end) return false
        val args = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, start)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, end)
        }
        if (!body.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, args)) return false
        sleepQuiet(250)
        return body.performAction(AccessibilityNodeInfo.ACTION_CUT)
    }

    /** commitText [s] through our in-app IME (base64 so any Chinese survives). */
    private fun commitText(s: String) {
        val b64 = Base64.encodeToString(s.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
        sendIme(AgentInputMethodService.ACTION_B64, b64)
    }

    /** Fire a command at our in-app IME (same package — a direct broadcast). */
    private fun sendIme(action: String, b64: String?) {
        val intent = Intent(action).apply {
            setPackage(service.packageName)
            if (b64 != null) putExtra("msg", b64)
        }
        service.sendBroadcast(intent)
    }

    /** Like [tapTopicSuggestion] but scans every window — the 话题 suggestion list
     * can render in an overlay window that isn't in rootInActiveWindow. Logs the
     * nearby candidate texts when nothing matches, to diagnose misses. */
    private fun tapTopicSuggestionAnyWindow(kw: String): Boolean {
        if (tapTopicSuggestion(service.rootInActiveWindow, kw)) return true
        for (window in service.windows) {
            val root = window.root ?: continue
            if (tapTopicSuggestion(root, kw)) return true
        }
        // Diagnostics: dump short non-editable texts so we can see what (if anything)
        // the suggestion panel is actually offering.
        val seen = collectNodes(service.rootInActiveWindow)
            .mapNotNull { it.text?.toString() }
            .filter { it.isNotBlank() && it.length <= 12 }
            .distinct()
            .take(12)
        android.util.Log.i("AgentTopics", "miss #$kw candidates=$seen")
        return false
    }

    /** Tap the topic suggestion row best matching [kw] (exact #kw / kw first). */
    private fun tapTopicSuggestion(root: AccessibilityNodeInfo?, kw: String): Boolean {
        val nodes = collectNodes(root)
        val exact = nodes.firstOrNull { n ->
            val t = n.text?.toString()?.trim()
            t == "#$kw" || t == kw
        }
        val target = exact ?: nodes.firstOrNull { n ->
            val t = n.text?.toString()
            t != null && t.contains(kw) && !n.isEditable && t.length <= kw.length + 8
        } ?: return false
        if (clickNodeOrParent(target)) return true
        val rect = Rect()
        target.getBoundsInScreen(rect)
        return rect.width() > 0 && rect.height() > 0 && tap(rect.exactCenterX(), rect.exactCenterY())
    }

    private fun sleepQuiet(ms: Long) {
        try {
            Thread.sleep(ms)
        } catch (_: InterruptedException) {
        }
    }

    fun setText(node: AccessibilityNodeInfo?, value: String): Boolean {
        if (node == null || !node.isEditable) return false
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val arguments = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                value,
            )
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
    }

    fun back(): Boolean =
        service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)

    fun swipeUp(): Boolean {
        val metrics = service.resources.displayMetrics
        val path = Path().apply {
            moveTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.75f)
            lineTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.25f)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 450))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    /** Scroll the list toward the TOP without triggering pull-to-refresh (starts
     * below the top edge). Used between groups so each search starts from the top. */
    fun scrollUp(): Boolean {
        val metrics = service.resources.displayMetrics
        val path = Path().apply {
            moveTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.35f)
            lineTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.72f)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 400))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    /** Pull-to-refresh: a slow downward drag from near the top. 抖音's 消息 list
     * shows a STALE cache on cold-start; this forces a server sync so the current
     * groups actually appear before we scroll-search for one. */
    fun pullToRefresh(): Boolean {
        val metrics = service.resources.displayMetrics
        val path = Path().apply {
            moveTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.30f)
            lineTo(metrics.widthPixels * 0.5f, metrics.heightPixels * 0.82f)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 600))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    /** Tap the top-right corner icon (抖音 我-page ☰ drawer menu). Device-independent:
     * the rightmost small clickable node in the top strip (avoids hardcoded coords
     * so it works on 小米/红米/OPPO alike). */
    fun tapTopRightCorner(): Boolean {
        val root = service.rootInActiveWindow ?: return false
        val m = service.resources.displayMetrics
        var best: AccessibilityNodeInfo? = null
        var bestX = -1
        for (node in collectNodes(root)) {
            if (node.isClickable != true) continue
            val r = Rect()
            node.getBoundsInScreen(r)
            if (r.centerY() in 0..(m.heightPixels * 12 / 100) &&
                r.centerX() > m.widthPixels * 80 / 100 &&
                r.width() in 1..(m.widthPixels / 3)
            ) {
                if (r.centerX() > bestX) { bestX = r.centerX(); best = node }
            }
        }
        val node = best ?: return false
        val r = Rect()
        node.getBoundsInScreen(r)
        return tap(r.exactCenterX(), r.exactCenterY())
    }

    private fun collectNodes(root: AccessibilityNodeInfo?): List<AccessibilityNodeInfo> {
        if (root == null) return emptyList()
        val result = mutableListOf<AccessibilityNodeInfo>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            result.add(node)
            repeat(node.childCount) { index ->
                node.getChild(index)?.let(queue::add)
            }
        }
        return result
    }

    private fun clickNodeOrParent(node: AccessibilityNodeInfo): Boolean {
        var current: AccessibilityNodeInfo? = node
        repeat(6) {
            if (current?.isClickable == true) {
                return current?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true
            }
            current = current?.parent
        }
        return false
    }
}
