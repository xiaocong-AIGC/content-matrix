package com.example.douyinagent.runtime

import com.example.douyinagent.accessibility.DouyinAccessibilityService
import com.example.douyinagent.execution.DouyinPage

object AgentRuntime {
    @Volatile
    var listener: Listener? = null

    @Volatile
    var accessibilityService: DouyinAccessibilityService? = null

    fun onAccessibilityReady(service: DouyinAccessibilityService) {
        accessibilityService = service
        listener?.onAccessibilityReady()
    }

    fun onAccessibilityStopped(service: DouyinAccessibilityService) {
        if (accessibilityService === service) {
            accessibilityService = null
        }
        listener?.onAccessibilityStopped()
    }

    fun onPageChanged(page: DouyinPage) {
        listener?.onPageChanged(page)
    }

    fun onAnomaly(message: String) {
        listener?.onAnomalyDetected(message)
    }

    interface Listener {
        fun onAccessibilityReady()
        fun onAccessibilityStopped()
        fun onPageChanged(page: DouyinPage)
        fun onAnomalyDetected(message: String)
    }
}
