package com.example.douyinagent.network

import com.example.douyinagent.BuildConfig
import com.example.douyinagent.accessibility.DouyinAccessibilityService
import com.example.douyinagent.model.AgentTask
import com.example.douyinagent.model.RegisteredDevice
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

class AgentApiClient(
    private val baseUrl: String,
    private val tokenProvider: () -> String,
) {
    fun register(
        deviceCode: String,
        name: String,
        androidVersion: String,
        douyinVersion: String?,
        provisionKey: String = "",
    ): RegisteredDevice {
        val response = post(
            "/devices/register",
            extraHeaders = if (provisionKey.isNotBlank())
                mapOf("X-Provision-Key" to provisionKey) else emptyMap(),
            body = JSONObject()
                .put("device_code", deviceCode)
                .put("name", name)
                .put("platform", "android")
                .put("agent_version", BuildConfig.VERSION_NAME)
                .put("android_version", androidVersion)
                .put("douyin_version", douyinVersion ?: JSONObject.NULL)
                .put(
                    "capabilities",
                    JSONArray(
                        listOf(
                            "task_pull",
                            "heartbeat",
                            "accessibility",
                            "page_recognition",
                            "screenshot_api30",
                        ),
                    ),
                ),
            authenticated = false,
        )
        return RegisteredDevice(
            id = response.getInt("id"),
            token = response.getString("agent_token"),
        )
    }

    /** (platform, nickname, accountId, loggedIn). loggedIn null = unknown. */
    data class AccountReport(
        val platform: String,
        val nickname: String?,
        val accountId: String?,
        val loggedIn: Boolean?,
    )

    fun reportAccounts(deviceId: Int, accounts: List<AccountReport>) {
        val arr = JSONArray()
        accounts.forEach { a ->
            arr.put(
                JSONObject()
                    .put("platform", a.platform)
                    .put("nickname", a.nickname ?: JSONObject.NULL)
                    .put("account_id", a.accountId ?: JSONObject.NULL)
                    .put("logged_in", a.loggedIn ?: JSONObject.NULL),
            )
        }
        post(
            "/agent/accounts",
            JSONObject().put("device_id", deviceId).put("accounts", arr),
        )
    }

    fun reportMetrics(
        deviceId: Int,
        platform: String,
        posts: List<DouyinAccessibilityService.PostMetricInfo>,
    ) {
        val arr = JSONArray()
        posts.forEach { p ->
            arr.put(
                JSONObject()
                    .put("title", p.title)
                    .put("body", p.body)
                    .put("views", p.views)
                    .put("likes", p.likes)
                    .put("collects", p.collects)
                    .put("comments", p.comments),
            )
        }
        post(
            "/agent/metrics",
            JSONObject()
                .put("device_id", deviceId)
                .put("platform", platform)
                .put("posts", arr),
        )
    }

    fun syncGroups(deviceId: Int, groups: List<DouyinAccessibilityService.GroupInfo>) {
        val arr = org.json.JSONArray()
        groups.forEach { g ->
            arr.put(
                JSONObject()
                    .put("name", g.name)
                    .put("member_count", g.memberCount ?: JSONObject.NULL)
                    .put("can_mention_all", false),
            )
        }
        post(
            "/agent/groups",
            JSONObject().put("device_id", deviceId).put("groups", arr),
        )
    }

    /** @return true when the server wants an on-demand 回采 (operator clicked 更新数据). */
    fun heartbeat(
        deviceId: Int,
        currentTaskId: Int?,
        nickname: String? = null,
        douyinId: String? = null,
        health: String? = null,
        healthMessage: String? = null,
        platform: String = "douyin",
        accessibilityOk: Boolean? = null,
    ): HeartbeatReply {
        val response = post(
            "/devices/$deviceId/heartbeat",
            JSONObject()
                .put("status", if (currentTaskId == null) "online" else "busy")
                .put("current_task_id", currentTaskId ?: JSONObject.NULL)
                .put("accessibility_ok", accessibilityOk ?: JSONObject.NULL)
                .put("agent_version", BuildConfig.VERSION_NAME)
                .put("platform", platform)
                .put("douyin_nickname", nickname ?: JSONObject.NULL)
                .put("douyin_id", douyinId ?: JSONObject.NULL)
                .put("health", health ?: JSONObject.NULL)
                .put("health_message", healthMessage ?: JSONObject.NULL),
        )
        return HeartbeatReply(
            refreshMetrics = response.optBoolean("refresh_metrics", false),
            refreshAccount = response.optBoolean("refresh_account", false),
        )
    }

    data class HeartbeatReply(val refreshMetrics: Boolean, val refreshAccount: Boolean)

    fun claim(deviceId: Int): AgentTask? {
        val response = post(
            "/agent/tasks/claim",
            JSONObject().put("device_id", deviceId),
        )
        val task = response.optJSONObject("task") ?: return null
        return AgentTask.fromJson(task)
    }

    fun renewLease(task: AgentTask, deviceId: Int) {
        post(
            "/agent/tasks/${task.id}/lease",
            leaseBody(task, deviceId),
        )
    }

    fun updateStatus(
        task: AgentTask,
        deviceId: Int,
        status: String,
        step: String,
        progress: Int,
        message: String,
        errorMessage: String? = null,
    ) {
        post(
            "/agent/tasks/${task.id}/status",
            leaseBody(task, deviceId)
                .put("status", status)
                .put("step", step)
                .put("progress", progress)
                .put("message", message)
                .put("error_message", errorMessage ?: JSONObject.NULL),
        )
    }

    fun appendLog(
        task: AgentTask,
        deviceId: Int,
        level: String,
        step: String,
        message: String,
        context: JSONObject = JSONObject(),
    ) {
        post(
            "/agent/tasks/${task.id}/logs",
            leaseBody(task, deviceId)
                .put("level", level)
                .put("step", step)
                .put("message", message)
                .put("context", context),
        )
    }

    fun uploadScreenshot(
        task: AgentTask,
        deviceId: Int,
        step: String,
        width: Int,
        height: Int,
        png: ByteArray,
    ) {
        val boundary = "AgentBoundary${UUID.randomUUID().toString().replace("-", "")}"
        val output = ByteArrayOutputStream()
        fun field(name: String, value: String) {
            output.write("--$boundary\r\n".toByteArray())
            output.write("Content-Disposition: form-data; name=\"$name\"\r\n\r\n".toByteArray())
            output.write(value.toByteArray())
            output.write("\r\n".toByteArray())
        }
        field("device_id", deviceId.toString())
        field("lease_token", task.leaseToken)
        field("step", step)
        field("width", width.toString())
        field("height", height.toString())
        output.write("--$boundary\r\n".toByteArray())
        output.write(
            "Content-Disposition: form-data; name=\"file\"; filename=\"screen.png\"\r\n"
                .toByteArray(),
        )
        output.write("Content-Type: image/png\r\n\r\n".toByteArray())
        output.write(png)
        output.write("\r\n--$boundary--\r\n".toByteArray())

        request(
            path = "/agent/tasks/${task.id}/screenshots",
            method = "POST",
            contentType = "multipart/form-data; boundary=$boundary",
            body = output.toByteArray(),
            authenticated = true,
        )
    }

    private fun leaseBody(task: AgentTask, deviceId: Int): JSONObject =
        JSONObject()
            .put("device_id", deviceId)
            .put("lease_token", task.leaseToken)

    /**
     * Pull the backend's selector overrides (if any). Returns null on failure so
     * the caller keeps the built-in defaults — Douyin UI changes can be patched
     * server-side without shipping a new APK.
     */
    fun fetchSelectors(): com.example.douyinagent.accessibility.SelectorConfig? {
        val connection =
            URL("$baseUrl/agent/selectors").openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 10_000
            connection.readTimeout = 15_000
            if (connection.responseCode in 200..299) {
                val txt = connection.inputStream.bufferedReader().use { it.readText() }
                com.example.douyinagent.accessibility.SelectorConfig.fromJson(JSONObject(txt))
            } else {
                null
            }
        } catch (_: Throwable) {
            null
        } finally {
            connection.disconnect()
        }
    }

    fun downloadTaskImage(taskId: Int): ByteArray? {
        val connection =
            URL("$baseUrl/agent/tasks/$taskId/image").openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 10_000
            connection.readTimeout = 20_000
            if (connection.responseCode in 200..299) {
                connection.inputStream.use { it.readBytes() }
            } else {
                null
            }
        } catch (_: Throwable) {
            null
        } finally {
            connection.disconnect()
        }
    }

    private fun post(
        path: String,
        body: JSONObject,
        authenticated: Boolean = true,
        extraHeaders: Map<String, String> = emptyMap(),
    ): JSONObject {
        val response = request(
            path = path,
            method = "POST",
            contentType = "application/json; charset=utf-8",
            body = body.toString().toByteArray(Charsets.UTF_8),
            authenticated = authenticated,
            extraHeaders = extraHeaders,
        )
        return if (response.isBlank()) JSONObject() else JSONObject(response)
    }

    private fun request(
        path: String,
        method: String,
        contentType: String,
        body: ByteArray,
        authenticated: Boolean,
        extraHeaders: Map<String, String> = emptyMap(),
    ): String {
        val connection = URL("$baseUrl$path").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 10_000
            connection.readTimeout = 15_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", contentType)
            connection.setRequestProperty("Accept", "application/json")
            if (authenticated) {
                connection.setRequestProperty("X-Agent-Token", tokenProvider())
            }
            extraHeaders.forEach { (k, v) -> connection.setRequestProperty(k, v) }
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val response = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                throw AgentApiException(code, response)
            }
            return response
        } finally {
            connection.disconnect()
        }
    }
}

class AgentApiException(
    val statusCode: Int,
    response: String,
) : RuntimeException("Agent API failed ($statusCode): $response")
