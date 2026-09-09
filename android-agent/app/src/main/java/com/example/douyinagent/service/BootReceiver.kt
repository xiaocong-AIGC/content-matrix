package com.example.douyinagent.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

import com.example.douyinagent.data.AgentPreferences

/**
 * Brings the agent back online after a reboot (or app update) so a phone in the
 * matrix doesn't need anyone to open the app. Only auto-starts if the operator
 * had previously started it (autoStart flag) and a server URL is configured.
 *
 * Note: accessibility stays enabled across reboots; MediaProjection (screenshots)
 * does not survive a reboot — it re-arms when the app is next foregrounded (the
 * PC watchdog can do that), and the consent dialog now auto-confirms.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_LOCKED_BOOT_COMPLETED &&
            action != "android.intent.action.QUICKBOOT_POWERON" &&
            action != Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            return
        }
        val prefs = AgentPreferences(context)
        if (!prefs.autoStart || prefs.baseUrl.isBlank()) return

        val serviceIntent = Intent(context, AgentForegroundService::class.java)
            .setAction(AgentForegroundService.ACTION_START)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(serviceIntent)
        } else {
            context.startService(serviceIntent)
        }
    }
}
