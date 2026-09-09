package com.example.douyinagent.accessibility

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.inputmethodservice.InputMethodService
import android.os.Build
import android.view.KeyEvent
import android.view.View
import android.widget.TextView

/**
 * A minimal in-app IME (ADBKeyBoard-style) used ONLY to inject Unicode text at
 * the cursor — needed to type real 小红书 话题 tags, which require per-keystroke
 * Chinese input that appends (accessibility's SET_TEXT replaces the whole field
 * and `adb input text` can't type Chinese). The PC orchestrator switches to this
 * IME just for the topic step and switches back, so it never affects normal use.
 *
 * Commands (sent by the orchestrator via `am broadcast`):
 *   ADB_INPUT_TEXT --es msg "#上海买房"   → commitText at the cursor
 *   ADB_INPUT_CODE --ei code 66            → key event (66 = Enter, selects topic)
 */
class AgentInputMethodService : InputMethodService() {
    private var lastCommit = ""
    private var lastCommitAt = 0L

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val ic = currentInputConnection
            android.util.Log.i("AgentIME", "recv ${intent.action} ic=${ic != null}")
            when (intent.action) {
                ACTION_TEXT -> intent.getStringExtra("msg")?.let {
                    ic?.commitText(it, 1)
                }
                ACTION_B64 -> intent.getStringExtra("msg")?.let {
                    // Base64 avoids the shell mangling non-ASCII (Chinese 话题).
                    val decoded = String(
                        android.util.Base64.decode(it, android.util.Base64.NO_WRAP),
                        Charsets.UTF_8,
                    )
                    // Dedupe: the receiver can fire twice (two IME instances) — drop
                    // an identical commit within 800ms.
                    val now = System.currentTimeMillis()
                    if (decoded == lastCommit && now - lastCommitAt < 800) return
                    lastCommit = decoded
                    lastCommitAt = now
                    android.util.Log.i("AgentIME", "commit '$decoded' ic=${ic != null}")
                    ic?.commitText(decoded, 1)
                }
                ACTION_COMPOSE -> intent.getStringExtra("msg")?.let {
                    // Composing (not final) text — leaves an editing span XHS can
                    // REPLACE when the topic suggestion is tapped (commitText left a
                    // gray-plaintext leftover beside the blue tag). Mirrors how a
                    // real keyboard feeds a 话题 query.
                    val decoded = String(
                        android.util.Base64.decode(it, android.util.Base64.NO_WRAP),
                        Charsets.UTF_8,
                    )
                    val now = System.currentTimeMillis()
                    if (decoded == lastCommit && now - lastCommitAt < 800) return
                    lastCommit = decoded
                    lastCommitAt = now
                    android.util.Log.i("AgentIME", "compose '$decoded' ic=${ic != null}")
                    // Keep it as a composing region so ACTION_TRIM can clear exactly
                    // this span after the tag is inserted.
                    ic?.setComposingText(decoded, 1)
                }
                ACTION_TRIM -> {
                    // After XHS appends the real (blue) tag, the typed "#kw" is still
                    // the IME's COMPOSING region (an a11y ACTION_CLICK doesn't finish
                    // it like a touch would). Replacing the composing region with ""
                    // deletes EXACTLY that span — no absolute offsets, which scramble
                    // once tag spans are in the field. If the region was already
                    // finalized, this is a harmless no-op (leftover stays, not corrupt).
                    val ic2 = currentInputConnection
                    if (ic2 != null) {
                        ic2.setComposingText("", 1)
                        ic2.finishComposingText()
                        android.util.Log.i("AgentIME", "trim composing")
                    }
                }
                ACTION_FINISH -> {
                    // Finalize the composing query into plain text — the degraded
                    // fallback when no 话题 suggestion was found to tap (a visible
                    // "#kw" is better than the topic vanishing).
                    currentInputConnection?.finishComposingText()
                }
                ACTION_CODE -> {
                    val code = intent.getIntExtra("code", 0)
                    if (code != 0) {
                        currentInputConnection?.sendKeyEvent(
                            KeyEvent(KeyEvent.ACTION_DOWN, code),
                        )
                        currentInputConnection?.sendKeyEvent(
                            KeyEvent(KeyEvent.ACTION_UP, code),
                        )
                    }
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        val filter = IntentFilter().apply {
            addAction(ACTION_TEXT)
            addAction(ACTION_B64)
            addAction(ACTION_CODE)
            addAction(ACTION_COMPOSE)
            addAction(ACTION_TRIM)
            addAction(ACTION_FINISH)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(receiver, filter)
        }
    }

    // A tiny placeholder view — we never show a real keyboard; input comes from
    // the broadcast. Keep it minimal so it doesn't cover the screen.
    override fun onCreateInputView(): View = TextView(this).apply {
        text = "Agent 输入法（自动化中）"
        setPadding(24, 16, 24, 16)
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(receiver) }
        super.onDestroy()
    }

    companion object {
        const val ACTION_TEXT = "ADB_INPUT_TEXT"
        const val ACTION_B64 = "ADB_INPUT_B64"
        const val ACTION_CODE = "ADB_INPUT_CODE"
        const val ACTION_COMPOSE = "ADB_INPUT_COMPOSE"
        const val ACTION_TRIM = "ADB_INPUT_TRIM"
        const val ACTION_FINISH = "ADB_INPUT_FINISH"
    }
}
