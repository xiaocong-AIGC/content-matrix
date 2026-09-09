package com.example.douyinagent.model

import org.json.JSONArray
import org.json.JSONObject

data class RegisteredDevice(
    val id: Int,
    val token: String,
)

data class AgentTask(
    val id: Int,
    val name: String,
    val platform: String,
    val publishType: String,
    val coverTitle: String,
    val publishTitle: String,
    val body: String,
    val topics: List<String>,
    val media: List<String>,
    val publishMode: String,
    val leaseToken: String,
    val mentionAll: Boolean = false,
    val targetGroups: List<String> = emptyList(),
    val hasImage: Boolean = false,
) {
    val manualConfirm: Boolean
        get() = publishMode != "auto_publish"

    val isGroupMessage: Boolean
        get() = publishType == "group_message"

    companion object {
        fun fromJson(json: JSONObject): AgentTask = AgentTask(
            id = json.getInt("id"),
            name = json.optString("name"),
            platform = json.optString("platform", "douyin"),
            publishType = json.optString("publish_type", "image_text"),
            coverTitle = json.optString("cover_title"),
            publishTitle = json.optString("publish_title"),
            body = json.optString("body"),
            topics = parseArray(json.optString("topics_json", "[]")),
            media = parseArray(json.optString("media_json", "[]")),
            publishMode = json.optString("publish_mode", "auto_publish"),
            leaseToken = json.getString("lease_token"),
            mentionAll = json.optBoolean("mention_all", false),
            targetGroups = parseArray(json.optString("target_groups_json", "[]")),
            hasImage = !json.optString("image_path").isNullOrBlank() &&
                json.optString("image_path") != "null",
        )

        private fun parseArray(value: String): List<String> {
            val array = JSONArray(value)
            return buildList {
                repeat(array.length()) { index ->
                    add(array.optString(index))
                }
            }
        }
    }
}
