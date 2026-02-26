package com.jarvis.assistant.feature.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationChannelGroup
import android.app.NotificationManager
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/** Channel group IDs that organize the four priority channels. */
private const val GROUP_PROACTIVE = "jarvis_proactive"
private const val GROUP_SYSTEM = "jarvis_system"

/**
 * Priority tiers for Jarvis proactive notifications.
 *
 * Each enum value maps to an Android [NotificationChannel] with the corresponding
 * importance level. URGENT bypasses Do Not Disturb for medication reminders and
 * emergencies; BACKGROUND is silent for sync status and learning updates.
 */
enum class NotificationPriority(
    val channelId: String,
    val channelName: String,
    val importance: Int,
    val groupId: String,
) {
    URGENT("jarvis_urgent", "Jarvis Urgent", NotificationManager.IMPORTANCE_HIGH, GROUP_PROACTIVE),
    IMPORTANT("jarvis_important", "Jarvis Important", NotificationManager.IMPORTANCE_DEFAULT, GROUP_PROACTIVE),
    ROUTINE("jarvis_routine", "Jarvis Routine", NotificationManager.IMPORTANCE_LOW, GROUP_PROACTIVE),
    BACKGROUND("jarvis_background", "Jarvis Background", NotificationManager.IMPORTANCE_MIN, GROUP_SYSTEM),
}

/**
 * Creates and manages the four Jarvis notification channels with groups.
 *
 * Call [createChannels] once at app startup (idempotent -- Android ignores
 * duplicate channel creation). Use [classifyPriority] to map desktop alert
 * types to the correct channel. Use [buildSummary] to bundle 3+ notifications.
 */
@Singleton
class NotificationChannelManager @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Create channel groups and all four notification channels. Safe to call
     * multiple times -- Android will not overwrite user-modified settings.
     */
    fun createChannels() {
        // Create groups first
        val groups = listOf(
            NotificationChannelGroup(GROUP_PROACTIVE, "Proactive Insights"),
            NotificationChannelGroup(GROUP_SYSTEM, "System & Sync"),
        )
        notificationManager.createNotificationChannelGroups(groups)

        val channels = listOf(
            NotificationChannel(
                NotificationPriority.URGENT.channelId,
                NotificationPriority.URGENT.channelName,
                NotificationPriority.URGENT.importance,
            ).apply {
                description = "Critical alerts that bypass Do Not Disturb (medication, emergencies)"
                group = NotificationPriority.URGENT.groupId
                setBypassDnd(true)
                enableVibration(true)
                enableLights(true)
                setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI, null)
            },
            NotificationChannel(
                NotificationPriority.IMPORTANT.channelId,
                NotificationPriority.IMPORTANT.channelName,
                NotificationPriority.IMPORTANT.importance,
            ).apply {
                description = "Important alerts (meeting prep, bill reminders)"
                group = NotificationPriority.IMPORTANT.groupId
                enableVibration(true)
                enableLights(true)
                setSound(android.provider.Settings.System.DEFAULT_NOTIFICATION_URI, null)
            },
            NotificationChannel(
                NotificationPriority.ROUTINE.channelId,
                NotificationPriority.ROUTINE.channelName,
                NotificationPriority.ROUTINE.importance,
            ).apply {
                description = "Routine updates (daily summaries, weekly reports)"
                group = NotificationPriority.ROUTINE.groupId
                enableVibration(false)
                enableLights(false)
                setSound(null, null)
            },
            NotificationChannel(
                NotificationPriority.BACKGROUND.channelId,
                NotificationPriority.BACKGROUND.channelName,
                NotificationPriority.BACKGROUND.importance,
            ).apply {
                description = "Background information (learning updates, sync status)"
                group = NotificationPriority.BACKGROUND.groupId
                enableVibration(false)
                enableLights(false)
                setSound(null, null)
            },
        )

        channels.forEach { notificationManager.createNotificationChannel(it) }
        Log.i(TAG, "Created ${groups.size} groups and ${channels.size} notification channels")
    }

    /** Get the Android channel ID string for the given priority. */
    fun getChannelId(priority: NotificationPriority): String = priority.channelId

    /**
     * Map a desktop alert type string to a [NotificationPriority].
     *
     * Unknown alert types default to [NotificationPriority.BACKGROUND].
     */
    fun classifyPriority(alertType: String): NotificationPriority {
        return when (alertType.lowercase()) {
            "medication_reminder", "emergency", "urgent_bill", "security_alert" ->
                NotificationPriority.URGENT

            "meeting_prep", "bill_reminder", "schedule_conflict", "important_task" ->
                NotificationPriority.IMPORTANT

            "daily_briefing", "weekly_summary", "spend_report", "habit_nudge" ->
                NotificationPriority.ROUTINE

            else -> NotificationPriority.BACKGROUND
        }
    }

    /**
     * Build a summary notification that bundles multiple child notifications.
     *
     * Call this when there are 3+ active notifications in the same channel to
     * collapse them into a single group summary. The summary uses InboxStyle.
     *
     * @param groupKey A shared group key for child notifications in this bundle.
     * @param priority The channel priority to post the summary on.
     * @param lines Preview lines to show in the collapsed summary (up to 6).
     * @return A ready-to-post [Notification] with the summary flag set.
     */
    fun buildSummary(
        groupKey: String,
        priority: NotificationPriority,
        lines: List<String>,
    ): Notification {
        val style = NotificationCompat.InboxStyle()
            .setBigContentTitle("Jarvis — ${lines.size} updates")
        lines.take(6).forEach { style.addLine(it) }
        if (lines.size > 6) {
            style.setSummaryText("+${lines.size - 6} more")
        }

        return NotificationCompat.Builder(context, priority.channelId)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Jarvis")
            .setContentText("${lines.size} new updates")
            .setStyle(style)
            .setGroup(groupKey)
            .setGroupSummary(true)
            .setAutoCancel(true)
            .build()
    }

    companion object {
        private const val TAG = "NotifChannelMgr"

        /** Minimum number of active notifications before showing a summary. */
        const val SUMMARY_THRESHOLD = 3
    }
}
