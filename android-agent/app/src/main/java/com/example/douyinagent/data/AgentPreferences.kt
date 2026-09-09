package com.example.douyinagent.data

import android.content.Context
import android.os.Build
import android.provider.Settings

class AgentPreferences(private val context: Context) {
    private val preferences =
        context.getSharedPreferences("douyin_agent", Context.MODE_PRIVATE)

    var baseUrl: String
        get() = preferences.getString(KEY_BASE_URL, DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        set(value) = preferences.edit().putString(KEY_BASE_URL, normalizeBaseUrl(value)).apply()

    var deviceName: String
        get() = preferences.getString(KEY_DEVICE_NAME, Build.MODEL) ?: Build.MODEL
        set(value) = preferences.edit().putString(KEY_DEVICE_NAME, value.trim()).apply()

    var deviceId: Int
        get() = preferences.getInt(KEY_DEVICE_ID, 0)
        set(value) = preferences.edit().putInt(KEY_DEVICE_ID, value).apply()

    var agentToken: String
        get() = preferences.getString(KEY_AGENT_TOKEN, "") ?: ""
        set(value) = preferences.edit().putString(KEY_AGENT_TOKEN, value).apply()

    // Pre-shared key sent on device registration (set during provisioning).
    var provisionKey: String
        get() = preferences.getString(KEY_PROVISION_KEY, "") ?: ""
        set(value) = preferences.edit().putString(KEY_PROVISION_KEY, value).apply()

    var lastStatus: String
        get() = preferences.getString(KEY_LAST_STATUS, "Agent 尚未启动") ?: "Agent 尚未启动"
        set(value) = preferences.edit().putString(KEY_LAST_STATUS, value).apply()

    // True once the operator has started the agent; drives auto-restart on boot
    // so a rebooted phone comes back online without anyone tapping the app.
    var autoStart: Boolean
        get() = preferences.getBoolean(KEY_AUTO_START, false)
        set(value) = preferences.edit().putBoolean(KEY_AUTO_START, value).apply()

    var douyinNickname: String
        get() = preferences.getString(KEY_DOUYIN_NICKNAME, "") ?: ""
        set(value) = preferences.edit().putString(KEY_DOUYIN_NICKNAME, value).apply()

    var douyinId: String
        get() = preferences.getString(KEY_DOUYIN_ID, "") ?: ""
        set(value) = preferences.edit().putString(KEY_DOUYIN_ID, value).apply()

    // Last 回采 time per platform — PERSISTED so reinstalling/restarting the app
    // doesn't re-trigger the daily 回采 (it's once per day, not once per launch).
    var lastXhsMetricsAt: Long
        get() = preferences.getLong(KEY_LAST_XHS_METRICS, 0L)
        set(value) = preferences.edit().putLong(KEY_LAST_XHS_METRICS, value).apply()

    var lastDouyinMetricsAt: Long
        get() = preferences.getLong(KEY_LAST_DY_METRICS, 0L)
        set(value) = preferences.edit().putLong(KEY_LAST_DY_METRICS, value).apply()

    val deviceCode: String
        get() {
            val existing = preferences.getString(KEY_DEVICE_CODE, null)
            if (!existing.isNullOrBlank()) return existing
            val androidId = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ANDROID_ID,
            )
            val fallback = "${Build.MANUFACTURER}-${Build.MODEL}"
            val generated = "android-${androidId.ifBlank { fallback }}"
            preferences.edit().putString(KEY_DEVICE_CODE, generated).apply()
            return generated
        }

    fun clearRegistration() {
        preferences.edit()
            .remove(KEY_DEVICE_ID)
            .remove(KEY_AGENT_TOKEN)
            .apply()
    }

    companion object {
        const val DEFAULT_BASE_URL = "http://192.168.1.100:8010/api/v1"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_DEVICE_CODE = "device_code"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_AGENT_TOKEN = "agent_token"
        private const val KEY_PROVISION_KEY = "provision_key"
        private const val KEY_LAST_STATUS = "last_status"
        private const val KEY_AUTO_START = "auto_start"
        private const val KEY_DOUYIN_NICKNAME = "douyin_nickname"
        private const val KEY_DOUYIN_ID = "douyin_id"
        private const val KEY_LAST_XHS_METRICS = "last_xhs_metrics_at"
        private const val KEY_LAST_DY_METRICS = "last_dy_metrics_at"

        fun normalizeBaseUrl(value: String): String =
            value.trim().trimEnd('/')
    }
}
