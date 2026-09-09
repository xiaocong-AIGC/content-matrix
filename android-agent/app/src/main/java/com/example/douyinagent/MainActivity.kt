package com.example.douyinagent

import android.app.Activity
import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.example.douyinagent.data.AgentPreferences
import com.example.douyinagent.service.AgentForegroundService

class MainActivity : Activity() {
    private lateinit var preferences: AgentPreferences
    private lateinit var serverInput: EditText
    private lateinit var nameInput: EditText
    private lateinit var statusText: TextView
    private val handler = Handler(Looper.getMainLooper())
    private val statusUpdater = object : Runnable {
        override fun run() {
            statusText.text = preferences.lastStatus
            handler.postDelayed(this, 1_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        preferences = AgentPreferences(this)
        setContentView(buildContent())
        applyProvisioning(intent)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        applyProvisioning(intent)
    }

    /**
     * Zero-touch provisioning via adb: a PC/Mac can push the server URL + device
     * name (and auto-start) without anyone typing on the phone, e.g.
     *   am start -n .../.MainActivity -e base_url "http://192.168.1.8:8010/api/v1" \
     *            -e device_name "MI9-01" --ez auto_start true
     */
    private fun applyProvisioning(intent: Intent?) {
        intent ?: return
        val url = intent.getStringExtra("base_url")
        val name = intent.getStringExtra("device_name")
        val provisionKey = intent.getStringExtra("provision_key")
        val auto = intent.getBooleanExtra("auto_start", false)
        // ⚠ 这里以前只看 url/name/provisionKey，三个都为空就 return —— 于是
        // `am start -n .../.MainActivity --ez auto_start true`（不带任何配置、只想把
        // Agent 拉起来）会在这一行被挡掉，前台服务根本不会启动。后果是**每次刷完
        // APK 都必须重启手机或人工点一下「保存配置并启动」**，37 台就是 1.5 小时。
        // auto_start 本身就是一个有效意图，不能被"没带配置"挡住。
        if (url.isNullOrBlank() && name.isNullOrBlank() && provisionKey.isNullOrBlank() && !auto) return
        if (!provisionKey.isNullOrBlank()) {
            preferences.provisionKey = provisionKey
            preferences.clearRegistration() // re-register with the key
        }
        if (!url.isNullOrBlank()) {
            val newUrl = AgentPreferences.normalizeBaseUrl(url)
            if (newUrl != preferences.baseUrl) preferences.clearRegistration()
            preferences.baseUrl = newUrl
            serverInput.setText(newUrl)
        }
        if (!name.isNullOrBlank()) {
            preferences.deviceName = name
            nameInput.setText(name)
        }
        if (auto) {
            preferences.autoStart = true
            requestNotificationPermission()
            val serviceIntent = Intent(this, AgentForegroundService::class.java)
                .setAction(AgentForegroundService.ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
            requestScreenCapture()
            preferences.lastStatus = "已通过 adb 自动配置并启动"
        }
    }

    override fun onResume() {
        super.onResume()
        handler.post(statusUpdater)
    }

    override fun onPause() {
        handler.removeCallbacks(statusUpdater)
        super.onPause()
    }

    private fun buildContent(): ScrollView {
        val density = resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()

        val content = LinearLayout(this).apply {
            // Take initial focus so the EditTexts don't auto-pop the keyboard
            // every time the screen opens (e.g. when the agent returns to front).
            isFocusableInTouchMode = true
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(40), dp(24), dp(40))
            setBackgroundColor(PAPER)
        }
        content.addView(TextView(this).apply {
            text = "CONTENT MATRIX PLATFORM"
            textSize = 11f
            setTextColor(CORAL)
            letterSpacing = 0.2f
        })
        content.addView(TextView(this).apply {
            text = "图文矩阵营销平台 · 执行端"
            textSize = 27f
            setTextColor(INK)
            setPadding(0, dp(8), 0, dp(8))
        })
        content.addView(TextView(this).apply {
            // ⚠ 别在这句里写「验证码 / 安全验证 / 人脸验证 / 滑块验证」——
            // 页面识别器拿这几个词判断"抖音弹了安全验证"，而切 App 的那一瞬间
            // 读到的窗口可能还是本界面，于是自己把自己认成安全验证页。
            // 根因已在 DouyinAccessibilityService 里按窗口包名挡掉，这里是双保险。
            text = "连接任务后台，通过无障碍服务自动发布到真实抖音 / 小红书 App。只有遇到需要本人操作的页面时才要人工介入。"
            textSize = 14f
            setTextColor(MUTED)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, dp(28))
        })

        serverInput = editor("后端地址", preferences.baseUrl)
        nameInput = editor("设备名称", preferences.deviceName)
        content.addView(serverInput)
        content.addView(nameInput)

        statusText = TextView(this).apply {
            text = preferences.lastStatus
            textSize = 14f
            setTextColor(INK_SOFT)
            background = rounded(WHITE, dp(12), LINE, dp(1))
            setPadding(dp(18), dp(16), dp(18), dp(16))
        }
        content.addView(
            statusText,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(16)
                bottomMargin = dp(16)
            },
        )

        content.addView(actionButton("保存配置并启动 Agent", true) {
            val newUrl = AgentPreferences.normalizeBaseUrl(serverInput.text.toString())
            if (newUrl != preferences.baseUrl) {
                preferences.clearRegistration()
            }
            preferences.baseUrl = newUrl
            preferences.deviceName = nameInput.text.toString().ifBlank { Build.MODEL }
            preferences.autoStart = true // resume on reboot
            hideKeyboard() // config saved — don't keep the keyboard up
            requestNotificationPermission()
            val serviceIntent = Intent(this, AgentForegroundService::class.java)
                .setAction(AgentForegroundService.ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
            requestScreenCapture()
        })
        content.addView(actionButton("授权屏幕截图（录屏）", false) {
            requestScreenCapture()
        })
        content.addView(actionButton("打开无障碍设置", false) {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        })
        content.addView(actionButton("保持后台运行（电池白名单）", false) {
            requestIgnoreBatteryOptimizations()
        })
        content.addView(actionButton("停止 Agent", false) {
            preferences.autoStart = false // don't resume on reboot
            startService(
                Intent(this, AgentForegroundService::class.java)
                    .setAction(AgentForegroundService.ACTION_STOP),
            )
        })

        content.addView(TextView(this).apply {
            text = "连接提示\n• 手机与电脑/服务器需在同一局域网，后端地址填服务器局域网 IP（如 http://192.168.1.8:8010/api/v1），不要填 127.0.0.1。\n• 多机保活：点上面「保持后台运行」加电池白名单；并在 系统设置→应用→本应用→自启动 里手动打开（MIUI 自启动无法由程序代开）。开启后重启手机会自动恢复运行。"
            textSize = 12f
            setTextColor(MUTED)
            setPadding(0, dp(24), 0, 0)
        })

        return ScrollView(this).apply {
            setBackgroundColor(PAPER)
            addView(content)
        }
    }

    private fun editor(label: String, value: String) = EditText(this).apply {
        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()
        hint = label
        setHintTextColor(MUTED)
        setText(value)
        setTextColor(INK)
        setSingleLine(true)
        background = rounded(WHITE, dp(10), LINE_STRONG, dp(1))
        setPadding(dp(16), dp(14), dp(16), dp(14))
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = dp(12) }
    }

    private fun actionButton(
        label: String,
        primary: Boolean,
        action: () -> Unit,
    ) = Button(this).apply {
        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()
        text = label
        isAllCaps = false
        textSize = 15f
        stateListAnimator = null
        setTextColor(if (primary) WHITE else INK)
        background =
            if (primary) rounded(CORAL, dp(12))
            else rounded(WHITE, dp(12), LINE_STRONG, dp(1))
        setOnClickListener { action() }
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply {
            topMargin = dp(2)
            bottomMargin = dp(10)
        }
    }

    /** A rounded-rect background, optionally with a 1px brand border. */
    private fun rounded(
        fill: Int,
        radius: Int,
        stroke: Int? = null,
        strokeWidth: Int = 0,
    ) = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        setColor(fill)
        cornerRadius = radius.toFloat()
        if (stroke != null && strokeWidth > 0) setStroke(strokeWidth, stroke)
    }

    private fun requestScreenCapture() {
        val manager = getSystemService(Context.MEDIA_PROJECTION_SERVICE)
            as MediaProjectionManager
        startActivityForResult(manager.createScreenCaptureIntent(), REQUEST_SCREEN_CAPTURE)
    }

    @Deprecated("Activity result API; sufficient for this single capture consent")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_SCREEN_CAPTURE && resultCode == RESULT_OK && data != null) {
            startService(
                Intent(this, AgentForegroundService::class.java)
                    .setAction(AgentForegroundService.ACTION_SCREEN_CAPTURE)
                    .putExtra(AgentForegroundService.EXTRA_RESULT_CODE, resultCode)
                    .putExtra(AgentForegroundService.EXTRA_RESULT_DATA, data),
            )
            preferences.lastStatus = "屏幕截图已授权"
        }
    }

    private fun hideKeyboard() {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE)
            as android.view.inputmethod.InputMethodManager
        currentFocus?.let {
            imm.hideSoftInputFromWindow(it.windowToken, 0)
            it.clearFocus()
        }
    }

    private fun requestNotificationPermission() {
        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }
    }

    /**
     * Ask the OS to exempt the app from battery optimization so MIUI/Android
     * doesn't kill the foreground service in the background. (MIUI's separate
     * "自启动" toggle still has to be enabled by hand — see the connection tips.)
     */
    private fun requestIgnoreBatteryOptimizations() {
        val pm = getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        android.net.Uri.parse("package:$packageName"),
                    ),
                )
            } catch (_: Exception) {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        } else {
            preferences.lastStatus = "已在电池白名单"
        }
    }

    private companion object {
        const val REQUEST_SCREEN_CAPTURE = 200
        // PC-console brand palette (paper / coral / ink) for a consistent look.
        val PAPER = Color.rgb(244, 242, 235)
        val WHITE = Color.rgb(255, 254, 250)
        val INK = Color.rgb(23, 22, 17)
        val INK_SOFT = Color.rgb(52, 50, 44)
        val MUTED = Color.rgb(128, 125, 115)
        val CORAL = Color.rgb(242, 79, 61)
        val LINE = Color.rgb(214, 212, 205)
        val LINE_STRONG = Color.rgb(190, 188, 180)
    }
}
