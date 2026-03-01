package com.jarvis.assistant.feature.notifications

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jarvis.assistant.MainActivity
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import dagger.hilt.android.qualifiers.ApplicationContext
import org.json.JSONObject
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Represents a single proactive alert received from the desktop engine.
 */
data class ProactiveAlert(
    /** Unique alert ID from the desktop. */
    val id: String,
    /** Alert type (e.g. "medication_reminder", "meeting_prep"). */
    val type: String,
    val title: String,
    val body: String,
    val priority: NotificationPriority,
    /** Grouping key for batching related alerts. */
    val groupKey: String,
    val receivedAt: Long = System.currentTimeMillis(),
)

/**
 * Polls the desktop engine for pending proactive alerts and posts them
 * as Android notifications through the correct priority channel.
 *
 * Called from [JarvisService]'s sync loop. Respects the current context
 * filter (set by [ContextAdjuster]) to suppress non-urgent notifications
 * during meetings, driving, etc.
 */
@Singleton
class ProactiveAlertReceiver @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val channelManager: NotificationChannelManager,
    private val batcher: NotificationBatcher,
    private val notificationLearner: NotificationLearner,
    @ApplicationContext private val context: Context,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Poll desktop for pending proactive alerts and post them.
     *
     * Uses the existing /command endpoint with a known phrase to retrieve
     * pending proactive alerts as JSON lines in stdout_tail.
     */
    suspend fun checkAndPost() {
        // Bug 5 fix: Respect the proactive alerts toggle from Settings
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_PROACTIVE_ALERTS_ENABLED, true)) {
            Log.d(TAG, "Proactive alerts disabled in settings, skipping")
            return
        }

        try {
            val response = apiClient.api().sendCommand(
                CommandRequest(
                    text = "Jarvis, show pending proactive alerts",
                    execute = false,
                ),
            )

            if (!response.ok) return

            val alerts = parseAlerts(response.stdoutTail)
            for (baseAlert in alerts) {
                // Bug 9 fix: Apply learned priority adjustments
                val adjustedPriority = notificationLearner.getAdjustedPriority(
                    baseAlert.type,
                    baseAlert.priority,
                )
                val alert = baseAlert.copy(priority = adjustedPriority)

                // Check context filter before posting
                val filter = getCurrentFilter()
                if (!shouldPost(alert.priority, filter)) {
                    Log.d(TAG, "Suppressed ${alert.type} notification (filter=$filter)")
                    continue
                }

                val batched = batcher.addAndFlush(alert)
                if (batched.isNotEmpty()) {
                    for (batch in batched) {
                        postBatchNotification(batch)
                    }
                } else {
                    // Not enough to batch yet -- post individual notification
                    // and remove from buffer to prevent duplicate when batch flushes later
                    postNotification(alert)
                    batcher.removeAlert(alert.groupKey, alert)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to check proactive alerts: ${e.message}")
        }
    }

    /**
     * Determine if a notification should be posted given the current context filter
     * and notification priority.
     */
    fun shouldPost(priority: NotificationPriority, filter: String): Boolean {
        return when (filter) {
            "emergency_only", "urgent_only" -> priority == NotificationPriority.URGENT
            "urgent_read_aloud" -> priority == NotificationPriority.URGENT
            "all" -> true
            else -> true
        }
    }

    /** Read the current notification filter from SharedPreferences. */
    fun getCurrentFilter(): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_NOTIFICATION_FILTER, "all") ?: "all"
    }

    /**
     * Post a single proactive alert as an Android notification.
     */
    fun postNotification(alert: ProactiveAlert) {
        if (!hasNotificationPermission()) return

        val channelId = channelManager.getChannelId(alert.priority)

        val tapIntent = PendingIntent.getActivity(
            context, alert.id.hashCode(),
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )

        val builder = NotificationCompat.Builder(context, channelId)
            .setContentTitle(alert.title)
            .setContentText(alert.body)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setGroup(alert.groupKey)
            .setAutoCancel(true)
            .setContentIntent(tapIntent)
            .setWhen(alert.receivedAt)
            .addExtras(android.os.Bundle().apply {
                putString("jarvis_alert_type", alert.type)
            })

        if (alert.priority == NotificationPriority.URGENT) {
            builder.setCategory(NotificationCompat.CATEGORY_ALARM)
            builder.setPriority(NotificationCompat.PRIORITY_HIGH)
            // Full-screen intent for heads-up display
            builder.setFullScreenIntent(tapIntent, true)
        }

        notificationManager.notify(alert.id.hashCode(), builder.build())
        Log.d(TAG, "Posted ${alert.priority.name} notification: ${alert.title}")
    }

    /**
     * Post a batched notification using InboxStyle.
     */
    private fun postBatchNotification(batch: BatchedNotification) {
        if (!hasNotificationPermission()) return

        val channelId = channelManager.getChannelId(batch.priority)

        val tapIntent = PendingIntent.getActivity(
            context, batch.groupKey.hashCode(),
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )

        val inboxStyle = NotificationCompat.InboxStyle()
            .setBigContentTitle("${batch.alerts.size} Jarvis alerts")
        for (alert in batch.alerts.take(MAX_INBOX_LINES)) {
            inboxStyle.addLine("${alert.title}: ${alert.body}")
        }
        if (batch.alerts.size > MAX_INBOX_LINES) {
            inboxStyle.setSummaryText("+${batch.alerts.size - MAX_INBOX_LINES} more")
        }

        val builder = NotificationCompat.Builder(context, channelId)
            .setContentTitle(batch.summary)
            .setContentText("${batch.alerts.size} related alerts")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setGroup(batch.groupKey)
            .setGroupSummary(true)
            .setAutoCancel(true)
            .setContentIntent(tapIntent)
            .setStyle(inboxStyle)

        if (batch.priority == NotificationPriority.URGENT) {
            builder.setCategory(NotificationCompat.CATEGORY_ALARM)
            builder.setPriority(NotificationCompat.PRIORITY_HIGH)
        }

        notificationManager.notify(batch.groupKey.hashCode(), builder.build())
        Log.d(TAG, "Posted batch notification: ${batch.summary}")
    }

    /**
     * Parse desktop command stdout_tail lines into [ProactiveAlert] objects.
     *
     * Expected format: each line is a JSON object with keys:
     * id, type, title, body, group_key
     */
    private fun parseAlerts(stdoutTail: List<String>): List<ProactiveAlert> {
        val alerts = mutableListOf<ProactiveAlert>()
        for (line in stdoutTail) {
            try {
                val trimmed = line.trim()
                if (!trimmed.startsWith("{")) continue
                val json = JSONObject(trimmed)
                val type = json.optString("type", "")
                val priority = channelManager.classifyPriority(type)

                alerts.add(
                    ProactiveAlert(
                        id = json.optString("id", System.currentTimeMillis().toString()),
                        type = type,
                        title = json.optString("title", "Jarvis Alert"),
                        body = json.optString("body", ""),
                        priority = priority,
                        groupKey = json.optString("group_key", "jarvis_default"),
                    ),
                )
            } catch (e: Exception) {
                Log.w(TAG, "Failed to parse alert line: $line")
            }
        }
        return alerts
    }

    private fun hasNotificationPermission(): Boolean {
        return if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            true
        }
    }

    companion object {
        private const val TAG = "ProactiveReceiver"
        private const val MAX_INBOX_LINES = 5

        /** SharedPreferences file for context-based notification filtering. */
        const val PREFS_NAME = "jarvis_prefs"

        /** Key for the current notification filter set by ContextAdjuster. */
        const val KEY_NOTIFICATION_FILTER = "context_notification_filter"

        /** Key for the proactive alerts enabled toggle (matches SettingsViewModel). */
        const val KEY_PROACTIVE_ALERTS_ENABLED = "proactive_alerts_enabled"
    }
}
