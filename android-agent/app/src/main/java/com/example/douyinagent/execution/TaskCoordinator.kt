package com.example.douyinagent.execution

import android.view.accessibility.AccessibilityNodeInfo
import com.example.douyinagent.accessibility.AccessibilityActions
import com.example.douyinagent.accessibility.Selectors
import com.example.douyinagent.model.AgentTask

class TaskCoordinator(
    private val reporter: Reporter,
) {
    private val state = ExecutionStateMachine()
    private var firstUnknownAt = 0L
    private var recoveryAttempts = 0
    private var lastRecoveryAt = 0L
    // Once we're in the composer/publish flow, DON'T auto-recover (BACK / dismiss
    // would undo legit progress). The stuck cases we want to self-heal are all in
    // the early launch→entry phase (popups blocking HOME/entry).
    private var reachedEditor = false
    private var lastLoggedPage: DouyinPage? = null
    private var pausedReported = false
    private var publishTappedAt = 0L
    private var verifyTicks = 0
    private var declSeenAt = 0L
    private var resultShotTaken = false
    private var resultShotAt = 0L
    private var resultReported = false
    private var declarationDismissed = 0
    // Paced text-flow state (one action at a time, with waits) so we never
    // mad-tap 下一步 or screenshot before the page has loaded.
    private var composerFilled = false
    private var composerFilledAt = 0L
    private var composerNextAt = 0L
    private var composerFocusAt = 0L
    private var templateEnteredAt = 0L
    private var templateShotTaken = false
    private var templateNextAt = 0L

    fun start(task: AgentTask) {
        // Use the right platform's selector set (douyin vs xhs) for the whole flow.
        Selectors.usePlatform(task.platform)
        state.moveTo(ExecutionStep.LAUNCHING_DOUYIN)
        val appName = if (task.platform == "xhs") "小红书" else "抖音"
        reporter.log("info", "claimed", "开始执行任务 #${task.id}：${task.name}")
        reporter.status(
            "running",
            "launching_douyin",
            10,
            "任务已领取，正在打开$appName",
        )
        reporter.launchApp(task.platform)
    }

    fun onPage(
        task: AgentTask,
        page: DouyinPage,
        root: AccessibilityNodeInfo?,
        actions: AccessibilityActions,
    ) {
        // After auto-publish we wait a few seconds, capture the result screen,
        // and finish — regardless of which page Douyin lands on.
        if (state.step == ExecutionStep.VERIFYING_RESULT) {
            val now = System.currentTimeMillis()
            // 插桩：这一段是最后一个黑盒。任务点完发布之后就停在这里不动，
            // 而这里本该每 tick 都跑（tick 里有 reevaluate 强制重算页面）。
            // 每 3 次进来打一条，别刷屏。
            verifyTicks += 1
            if (verifyTicks % 3 == 1) {
                reporter.log(
                    "debug", "verifying",
                    "结果确认第 $verifyTicks 轮：页面=$page 截图已拍=$resultShotTaken " +
                        "已报结果=$resultReported 声明处理=$declarationDismissed 次 " +
                        "距点发布=${(now - publishTappedAt) / 1000}秒 " +
                        "窗口数=${actions.windowCount()} " +
                        "看到自主声明=${actions.textsMatchingAnyWindow("自主声明").isNotEmpty()}",
                )
            }
            // 小红书 may interpose a "以下情况需声明，无则直接发布" sheet between
            // tapping 发布笔记 and the note actually posting. We never add a
            // 声明, so just tap 发布笔记 again to proceed. Reset the result
            // timers so the screenshot waits for the REAL published frame.
            // Guard with a counter so a stuck sheet can't loop forever.
            if (!resultShotTaken && declarationDismissed < 3) {
                // ⚠ **必须按平台隔开**，否则这一条会把抖音的面板吃掉：
                // 抖音的面板标题是「为作品**添加声明**」—— 正好命中下面的
                // "添加声明"，于是走进小红书分支、去点一个抖音上不存在的
                // 「发布笔记」，然后**无条件 return**。抖音那段永远执行不到。
                // 而这个分支只在点中时计数、只在点中时写日志，所以表现是
                // **零日志、零计数、无限循环** —— 云机6015 卡了一整天就是这个。
                // 一个子串碰撞，找了四轮才找到。
                if (task.platform == "xhs" && (
                        actions.textsMatchingAnyWindow("需声明").isNotEmpty() ||
                            actions.textsMatchingAnyWindow("添加声明").isNotEmpty()
                        )
                ) {
                    // 同抖音那段：计尝试次数、无论成败都写日志，否则点不中就是无限循环
                    declarationDismissed += 1
                    val tapped = actions.clickTextAnyWindow(listOf("发布笔记")) ||
                        actions.tapByTextAnyWindow(listOf("发布笔记"))
                    reporter.log(
                        "info", "publish",
                        "小红书声明弹窗第 $declarationDismissed 次：发布笔记=$tapped",
                    )
                    if (tapped) publishTappedAt = now
                    return
                }
                // 抖音 interposes a "为作品添加自主声明" sheet on 发作品. We don't add a
                // 声明 → tap the top option「无需添加自主声明」then「发作品」(both are
                // visible together in the sheet, so one pass works; retries next tick).
                // ⚠ **必须跨窗口找**。这个声明面板是一个**独立窗口**，不在
                // `rootInActiveWindow` 里 —— 只看 declRoot 的话 hasText 永远是 false，
                // 于是这段处理一次都不会执行，任务就永远停在面板上。
                //
                // 2026-09-09 云机6015：连着三条任务卡死在这儿，一整天 0 篇。
                // uiautomator dump（会 dump 所有窗口）能看到「为作品添加自主声明 /
                // 无需添加自主声明 / …/ 发作品」，而 Agent 自己"看不见"。
                //
                // 这是同一个坑的第三次 —— `clickTextAnyWindow` 当初就是为
                // @ 选择器那个浮层加的，注释里写得很清楚，这里却还在用单窗口版本。
                if (task.platform != "xhs" &&
                    actions.textsMatchingAnyWindow("自主声明").isNotEmpty()
                ) {
                    // ⚠ 计数必须放在**进来就加**，不能只在点中时加。
                    // 原来是 `if (tapped) declarationDismissed += 1`，于是
                    // 「点不中」永远停在 0，那道 `< 3` 的护栏形同虚设 ——
                    // 每个 tick 原地重来一次，**一条日志都不写**，
                    // 任务就这么无限挂着（云机6015 实测 158 秒后仍是 0 次）。
                    if (declSeenAt == 0L) declSeenAt = now
                    declarationDismissed += 1
                    val noDecl = actions.clickTextAnyWindow(listOf("无需添加自主声明"))
                    Thread.sleep(400)
                    // 面板里的「发作品」是个 clickable=false 的 TextView，
                    // ACTION_CLICK 会往上找可点父节点 —— 找不到就得**用坐标兜底**，
                    // clickTextAnyWindow 本身没有坐标兜底，所以这里补一手。
                    val tapped = actions.clickTextAnyWindow(listOf("发作品")) ||
                        actions.tapByTextAnyWindow(listOf("发作品"))
                    reporter.log(
                        "info", "publish",
                        "自主声明面板第 $declarationDismissed 次：无需声明=$noDecl 发作品=$tapped",
                    )
                    if (tapped) {
                        publishTappedAt = now
                    } else if (declarationDismissed >= 3 || now - declSeenAt > 20_000) {
                        // 点不动就说清楚，别再耗着手机
                        reporter.screenshot("declaration_stuck")
                        state.moveTo(ExecutionStep.FAILED)
                        reporter.status(
                            "failed", "declaration_blocked", 0,
                            "抖音的自主声明面板挡着，试了 $declarationDismissed 次点不到「发作品」。" +
                                "屏上文字：[" +
                                actions.textsMatchingAnyWindow("声明").take(4).joinToString("/") + "]",
                        )
                    }
                    return
                }
            }
            // 1) Capture the result screen first. 2) Only AFTER a short grace
            // period (so the async MediaProjection capture + upload finishes)
            // report success — reporting it immediately tore down the task
            // (currentTask=null + returnToForeground → HOME) before the frame
            // arrived, so the result screenshot never回传ed.
            if (!resultShotTaken && now - publishTappedAt >= RESULT_DELAY_MS) {
                resultShotTaken = true
                resultShotAt = now
                reporter.screenshot("published")
            }
            if (resultShotTaken && !resultReported &&
                now - resultShotAt >= RESULT_UPLOAD_GRACE_MS
            ) {
                // ⚠ 报成功必须有**正面证据**，不能只数秒表。
                // 这一段名叫「验证结果」，但原来只是点完发布数 6+3 秒就无条件报
                // succeeded —— 从不看屏幕。声明面板挡着、风控弹窗、审核拦截、
                // 退回编辑页，抖音怎么回应都不影响结论：控制台全绿、内容出池，
                // 而账号上一篇都没有。**卡死至少后端 10 分钟能判出来，报绿永远判不出来。**
                val stillOnConfirm =
                    actions.textsMatchingAnyWindow(
                        Selectors.current.publishButtons.firstOrNull() ?: "发布",
                    ).isNotEmpty()
                if (page == DouyinPage.PUBLISH_SUCCESS || !stillOnConfirm) {
                    resultReported = true
                    reporter.status("succeeded", "completed", 100, "已自动发布，已回传结果截图")
                } else if (now - publishTappedAt >= VERIFY_TIMEOUT_MS) {
                    resultReported = true
                    state.moveTo(ExecutionStep.FAILED)
                    reporter.status(
                        "failed", "publish_not_confirmed", 0,
                        "点了发布，${VERIFY_TIMEOUT_MS / 1000} 秒后仍停在发布确认页。" +
                            "屏上带「发」的文字：[" +
                            actions.textsMatchingAnyWindow("发").take(6).joinToString("/") + "]",
                    )
                }
            }
            return
        }

        // Only log when the page actually changes, otherwise an unrecognized
        // screen floods the execution log with identical entries.
        if (page != lastLoggedPage) {
            reporter.log("debug", "page_recognition", "识别到页面：${page.name}")
            lastLoggedPage = page
        }
        if (page != DouyinPage.UNKNOWN) {
            firstUnknownAt = 0L
            pausedReported = false
            recoveryAttempts = 0
        }
        when (page) {
            DouyinPage.SECURITY_CHALLENGE -> {
                if (state.step < ExecutionStep.WAITING_CONFIRMATION) {
                    state.moveTo(ExecutionStep.WAITING_CONFIRMATION)
                    reporter.screenshot("security_challenge")
                    val hit = com.example.douyinagent.accessibility
                        .RecognitionHint.securityMatch
                    reporter.status(
                        "waiting_confirmation",
                        "security_challenge",
                        30,
                        "检测到安全验证，已停止自动操作，请人工处理" +
                            (if (hit.isNullOrBlank()) "" else "（屏幕上读到：$hit）"),
                    )
                }
            }
            DouyinPage.HOME -> {
                // Cold-started Douyin can ignore the first tap while still loading,
                // so keep tapping the shoot button until the camera page is reached
                // (which advances past this step). The button is custom-drawn, so
                // tap its coordinates first. Status is reported only once.
                if (state.step <= ExecutionStep.OPENING_PUBLISH_ENTRY) {
                    val candidates = Selectors.current.shootButton
                    val clicked = actions.tapByText(root, candidates) ||
                        actions.clickAnyText(root, candidates)
                    if (state.step < ExecutionStep.OPENING_PUBLISH_ENTRY) {
                        state.moveTo(ExecutionStep.OPENING_PUBLISH_ENTRY)
                        reporter.status(
                            "running",
                            "opening_publish_entry",
                            20,
                            if (clicked) "已在首页点击拍摄按钮" else "已到达首页，正在尝试打开拍摄",
                        )
                    }
                }
            }
            DouyinPage.PUBLISH_ENTRY -> {
                // Camera/record page: pick the 文字 (text) mode — never the 相册.
                // Retry until the composer appears.
                if (state.step <= ExecutionStep.SELECTING_PUBLISH_TYPE) {
                    val clicked = actions.tapByText(root, Selectors.current.publishEntryText) ||
                        actions.clickAnyText(root, Selectors.current.publishEntryText)
                    if (state.step < ExecutionStep.SELECTING_PUBLISH_TYPE) {
                        state.moveTo(ExecutionStep.SELECTING_PUBLISH_TYPE)
                        reporter.status(
                            "running",
                            "selecting_publish_type",
                            34,
                            if (clicked) "已选择「文字」发布模式" else "发布入口已识别，正在选择「文字」",
                        )
                    }
                }
            }
            DouyinPage.TEXT_COMPOSER -> {
                // Deliberate, one-step-at-a-time: type the text → dismiss the
                // keyboard → wait → tap 下一步 once (retry only every NAV_RETRY_MS
                // if we're still here). Never mad-tap.
                val now = System.currentTimeMillis()
                if (!composerFilled) {
                    if (now - composerFocusAt >= NAV_RETRY_MS) {
                        composerFocusAt = now
                        // tap-to-focus + fill (XHS lazily creates its EditText on tap).
                        val filled = actions.focusAndFill(
                            Selectors.current.composerHints,
                            composeBody(task),
                        )
                        if (filled) {
                            composerFilled = true
                            composerFilledAt = now
                            state.moveTo(ExecutionStep.EDITING_CONTENT)
                            reporter.status(
                                "running",
                                "composing_text",
                                60,
                                "已输入文字内容，进入下一步",
                            )
                        }
                    }
                } else if (now - composerFilledAt >= COMPOSER_SETTLE_MS &&
                    now - composerNextAt >= NAV_RETRY_MS
                ) {
                    composerNextAt = now
                    actions.clickAnyText(root, Selectors.current.templateNext) ||
                        actions.tapByText(root, Selectors.current.templateNext)
                }
            }
            DouyinPage.TEXT_TEMPLATE -> {
                // Use the system-recommended template: wait for it to load, then
                // screenshot, then tap 下一步 (retry every NAV_RETRY_MS).
                val now = System.currentTimeMillis()
                if (templateEnteredAt == 0L) {
                    templateEnteredAt = now
                    state.moveTo(ExecutionStep.REVIEWING)
                    reporter.status(
                        "running",
                        "selecting_template",
                        80,
                        "使用系统推荐模板，等待模板加载…",
                    )
                }
                if (now - templateEnteredAt >= TEMPLATE_LOAD_MS) {
                    if (!templateShotTaken) {
                        templateShotTaken = true
                        reporter.screenshot("selecting_template") // 模板加载后再回传
                    }
                    // The template renders the text into a styled graphic card, so
                    // the body text usually ISN'T in the accessibility tree — never
                    // gate 下一步 on hasText(body) or we stall here forever (this is
                    // exactly the "几秒不动" stall). Content was already entered and
                    // verified on the composer step (composerFilled), so just advance
                    // once the template has had time to render.
                    if (composerFilled && now - templateNextAt >= NAV_RETRY_MS) {
                        templateNextAt = now
                        actions.clickAnyText(root, listOf("下一步")) ||
                            actions.tapByText(root, listOf("下一步"))
                    }
                }
            }
            DouyinPage.MEDIA_PICKER -> {
                // The album page is not part of the text flow — hand to a human.
                if (state.step < ExecutionStep.WAITING_CONFIRMATION) {
                    state.moveTo(ExecutionStep.WAITING_CONFIRMATION)
                    reporter.screenshot("media_picker")
                    reporter.status(
                        "waiting_confirmation",
                        "selecting_media",
                        42,
                        "进入了相册页（非文字流程），已暂停并转交人工",
                    )
                }
            }
            DouyinPage.EDITOR -> {
                reachedEditor = true // past the early phase → stop auto-recovery
                actions.clickAnyText(root, listOf("下一步"))
            }
            DouyinPage.PUBLISH_CONFIRM -> {
                // Fill once and stop — never overwrite while a human reviews. The
                // XHS 话题 picker has long sleeps, so run filling on a worker thread
                // (this callback is the accessibility main thread → would ANR).
                if (state.step < ExecutionStep.WAITING_CONFIRMATION) {
                    state.moveTo(ExecutionStep.WAITING_CONFIRMATION)
                    // ⚠ **必须兜住异常**。这个线程负责把状态推到 VERIFYING_RESULT
                    // （moveTo 是 fillAndPublish 的最后一行）。中间任何一处抛异常，
                    // 线程就无声死掉：状态永远停在 WAITING_CONFIRMATION，
                    // onPage 每次进来都因为 `step < WAITING_CONFIRMATION` 为假而什么都不做，
                    // 于是任务挂在那里、手机一直被占着，**没有任何日志、没有任何报错**。
                    //
                    // 2026-09-09 云机6015 就是这样白占了 32 分钟，最后被无障碍熔断
                    // 顺手带走，报出来的原因还和真实情况无关。
                    Thread {
                        try {
                            fillAndPublish(task, actions)
                        } catch (error: Throwable) {
                            // 如实说出来，并把任务收掉 —— 手机要还给后面的任务
                            reporter.status(
                                "failed", "publish_crashed", 0,
                                "发布过程中出错：${error.javaClass.simpleName}" +
                                    "${error.message?.let { "：$it" } ?: ""}",
                            )
                            state.moveTo(ExecutionStep.FAILED)
                        }
                    }.start()
                }
            }
            DouyinPage.PUBLISH_SUCCESS -> {
                state.moveTo(ExecutionStep.COMPLETED)
                reporter.screenshot("completed")
                reporter.status("succeeded", "completed", 100, "已识别到发布成功页面")
            }
            DouyinPage.UNKNOWN -> {
                // Page transitions emit short bursts of UNKNOWN; only treat it as
                // a real stall if it persists, and never advance the execution
                // step here or it would block the next real page.
                // 读不到屏幕（无障碍正在重建）就直接跳过这一轮：既不计时也不
                // 消耗恢复次数。否则一次无障碍抖动就能把三次恢复机会烧光，
                // 然后报一个和真实原因无关的「页面无法识别」。
                if (root == null) return
                val now = System.currentTimeMillis()
                if (firstUnknownAt == 0L) firstUnknownAt = now
                // AUTO-RECOVERY before handing off to a human: dismiss a common
                // interstitial (升级/权限/青少年/引导 popups) if present, else press
                // BACK to climb back to a known page. Cheap, deterministic, on-
                // device — this fixes the HOME/UNKNOWN flapping that used to stick
                // tasks in waiting_confirmation and block the queue.
                if (!reachedEditor &&
                    now - firstUnknownAt >= RECOVERY_DELAY_MS &&
                    recoveryAttempts < MAX_RECOVERY_ATTEMPTS &&
                    now - lastRecoveryAt >= RECOVERY_INTERVAL_MS
                ) {
                    lastRecoveryAt = now
                    recoveryAttempts++
                    val dismissed = actions.clickAnyText(root, RECOVERY_DISMISS_LABELS)
                    if (!dismissed) actions.back()
                    reporter.log(
                        "info", "auto_recover",
                        "未知页面，自动恢复#$recoveryAttempts：${if (dismissed) "关闭弹窗" else "返回上一页"}",
                    )
                    return
                }
                // Recovery exhausted (or skipped because we're past the editor) and
                // still stuck → hand off to a human.
                if (now - firstUnknownAt >= UNKNOWN_TIMEOUT_MS &&
                    (reachedEditor || recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) &&
                    !pausedReported
                ) {
                    pausedReported = true
                    reporter.screenshot("unknown_page")
                    reporter.status(
                        "waiting_confirmation",
                        "unknown_page",
                        50,
                        "页面无法识别，自动恢复多次仍失败，转交人工处理",
                    )
                }
            }
        }
    }

    /**
     * Fills 标题 + 正文, adds 话题, and either waits for human review or auto-
     * publishes. Runs on a worker thread (the XHS 话题 picker sleeps a lot).
     */
    private fun fillAndPublish(task: AgentTask, actions: AccessibilityActions) {
        val titleFilled = actions.fillField(
            actions.root(), Selectors.current.publishTitleHints, task.publishTitle,
        )
        // 话题 strategy: append plain-text "#topic" to the 正文. This is 100%
        // reliable and never corrupts the post — the production default, in line
        // with "stability first".
        //
        // Real BLUE (traffic-pool-linked) tags are achievable via our in-app IME
        // (compose "#kw" → tap the suggestion → trim) and DO work for a single tag,
        // but across multiple topics XHS's tag insertion desyncs the IME's
        // cursor/composing state — later topics insert mid-text or stop registering
        // as a live query, scrambling the 正文. Until that is stable it stays behind
        // [enableRealXhsTopics] (default false) so it never risks a real account.
        // The mechanism lives in AccessibilityActions.typeXhsTopics (+ the in-app
        // IME) for continued R&D.
        val captionFilled: Boolean
        if (task.platform == "xhs" && enableRealXhsTopics && task.topics.isNotEmpty()) {
            captionFilled = actions.fillBodyField(actions.root(), bodyOnly(task))
            Thread.sleep(500)
            android.util.Log.i("AgentImeSet", "1") // orchestrator -> our IME
            Thread.sleep(2200) // let the orchestrator switch the IME
            actions.typeXhsTopics(task.topics) // agent drives the topic loop
            android.util.Log.i("AgentImeRestore", "1") // orchestrator -> user IME
            Thread.sleep(800)
        } else {
            captionFilled = actions.fillBodyField(actions.root(), captionWithTopics(task))
        }
        reporter.screenshot("waiting_confirmation")
        if (task.manualConfirm) {
            reporter.status(
                "waiting_confirmation", "waiting_confirmation", 96,
                "标题写入=$titleFilled，正文/话题写入=$captionFilled，请人工核对后点「发布」",
            )
        } else {
            // ⚠ **写入失败就不能发**。`captionFilled` 以前只拿来拼一句人工确认
            // 的提示文案，自动发布这条路上算完就扔了 —— 于是正文没写进去时，
            // 我们照样去点发布，把一条空内容发到真实账号上。
            //
            // `fillBodyField` 只有两种情况返回 false：找不到可编辑控件，
            // 或者 setText 被拒。正文本身是空的它返回 true，所以这里为 false
            // 一定是真的没写进去。发出去比失败严重得多 —— 失败会自动改期重发，
            // 空内容发出去只能人工去删。
            if (!captionFilled) {
                reporter.screenshot("caption_empty")
                reporter.status(
                    "failed", "caption_empty", 0,
                    "正文没能写进编辑框，已停在发布前，不会发出空内容",
                )
                state.moveTo(ExecutionStep.FAILED)
                return
            }
            // 标题不同：抖音图文的标题栏在有些版本上本来就不出现，写不进去不代表
            // 内容有问题，所以只记一条日志，不拦。
            if (!titleFilled && task.publishTitle.isNotBlank()) {
                reporter.log("warning", "publishing", "标题没能写进去，正文正常，继续发布")
            }
            reporter.status("running", "publishing", 98, "内容已就绪，自动点击发布")
            // ⚠ **必须看返回值**。这一下以前是
            //     `clickAnyText(...) || tapByText(...)`
            // 结果丢掉不管 —— 点没点中都当点中了，然后 moveTo(VERIFYING_RESULT)
            // 去等一个永远不会来的发布成功页。任务就那么挂着，直到别的东西
            // 顺手把它带走，而报出来的原因和真实情况毫无关系。
            //
            // 2026-09-09 云机6015 连着两条任务这样卡住：截图停在发布确认页、
            // 「发作品」按钮还在，一整天 0 篇。
            //（同一类错误在群发那条路上也犯过一次，见 openMessageList 的注释。）
            //
            // 重试三次并且每次**重新读一遍控件树**：上一帧的节点可能已经失效，
            // 拿旧的 root 点必然点空。
            // 插桩：这一段曾经"什么都不说"地卡死过（云机6015，连续 4 条任务）。
            // 已排除异常（外层 try/catch 什么都没报）、状态机顺序、tick 没跑。
            // 剩下的可能是某个无障碍调用**不返回**，所以每一步都留一条日志 ——
            // 下次再卡，日志会直接指出停在哪一行，不用再猜。
            var tapped = false
            for (attempt in 0 until 3) {
                reporter.log("debug", "publishing", "点发布：第 ${attempt + 1} 次，正在读控件树")
                val r = actions.root()
                reporter.log(
                    "debug", "publishing",
                    "点发布：控件树${if (r == null) "为空" else "已拿到"}，开始找按钮",
                )
                tapped = actions.clickAnyText(r, Selectors.current.publishButtons) ||
                    actions.tapByText(r, Selectors.current.publishButtons)
                reporter.log("debug", "publishing", "点发布：第 ${attempt + 1} 次结果=$tapped")
                if (tapped) break
                Thread.sleep(1200)
            }
            if (!tapped) {
                // 说清楚是"没点到按钮"，不是"发布失败" —— 前者要查选择器和页面，
                // 后者要查账号，处理动作完全不同。
                val seen = actions.textsMatching(actions.root(), "发")
                    .take(6).joinToString("/")
                reporter.status(
                    "failed", "publish_button_missing", 0,
                    "没能点到「${Selectors.current.publishButtons.firstOrNull() ?: "发布"}」" +
                        "，试了 3 次。当前屏上带「发」的文字：[$seen]",
                )
                return
            }
            publishTappedAt = System.currentTimeMillis()
            state.moveTo(ExecutionStep.VERIFYING_RESULT)
            reporter.log("info", "publishing", "已点发布，进入结果确认阶段")
        }
    }

    // 发布页正文 = 正文（不含话题；话题用 addXhsTopics 插成真标签）。
    private fun bodyOnly(task: AgentTask): String =
        task.body.ifBlank { task.coverTitle }.ifBlank { task.name }

    // 大字报：写文字页卡片上的文字 = 封面文字（与正文分开；话题不放这里）。
    private fun composeBody(task: AgentTask): String = task.coverTitle
        .ifBlank { task.body }
        .ifBlank { task.publishTitle }
        .ifBlank { task.name }

    // 发布页正文/文案 = 正文 + 话题（与大字报分开，正文可比卡片更长）。
    private fun captionWithTopics(task: AgentTask): String = buildString {
        append(task.body.ifBlank { task.coverTitle }.ifBlank { task.name })
        if (task.topics.isNotEmpty()) {
            if (isNotBlank()) append(" ")
            append(task.topics.joinToString(" ") { "#$it" })
        }
    }

    interface Reporter {
        fun launchApp(platform: String)
        fun status(status: String, step: String, progress: Int, message: String)
        fun log(level: String, step: String, message: String)
        fun screenshot(step: String)
    }

    companion object {
        // Real BLUE 小红书 话题 tags via the in-app IME (vs plain-text "#topic").
        // Kept as a kill-switch while we harden multi-topic stability.
        private const val enableRealXhsTopics = true

        // Generous so a slow cold-start of Douyin isn't mistaken for a stall.
        private const val UNKNOWN_TIMEOUT_MS = 15000L
        // Wait after tapping 发作品 before capturing the published result.
        private const val RESULT_DELAY_MS = 6000L
        // Grace after the result screenshot before reporting success — lets the
        // async MediaProjection capture + upload finish before the task tears
        // down (currentTask=null + returnToForeground), so the 截图 actually回传s.
        private const val RESULT_UPLOAD_GRACE_MS = 3000L
        // 点完发布最多等这么久还没离开确认页，就当没发出去 —— 宁可报失败
        // 让它重排，也不要报一个假的成功（那个连后端都判不出来）。
        private const val VERIFY_TIMEOUT_MS = 45_000L
        // After typing, let the keyboard collapse before tapping 下一步.
        private const val COMPOSER_SETTLE_MS = 1800L
        // Let the template page render (recommended template + strip) before
        // screenshotting and advancing. Generous so a SLOW network still has the
        // styled card loaded before we move to the publish page (the "模板没加载
        // 就跳到发布页" case).
        private const val TEMPLATE_LOAD_MS = 8000L
        // Minimum gap between repeated navigation taps on the same page, so a
        // missed tap is retried but the agent never mad-taps.
        private const val NAV_RETRY_MS = 5000L

        // Auto-recovery on UNKNOWN pages: wait a bit (skip brief transition
        // flickers), then up to N spaced attempts before handing off to a human.
        private const val RECOVERY_DELAY_MS = 2500L
        private const val RECOVERY_INTERVAL_MS = 2500L
        private const val MAX_RECOVERY_ATTEMPTS = 3
        // SAFE dismiss labels only — closing popups / going back. Deliberately NO
        // 确定/允许/同意/发布 (those could confirm an action or grant a permission).
        private val RECOVERY_DISMISS_LABELS = listOf(
            "关闭", "取消", "跳过", "我知道了", "知道了", "稍后再说", "以后再说",
            "暂不", "暂不更新", "残忍拒绝", "不再提示", "不再显示", "我再想想",
            "坚持退出", "退出", "不允许", "暂不开启", "返回",
        )
    }
}
