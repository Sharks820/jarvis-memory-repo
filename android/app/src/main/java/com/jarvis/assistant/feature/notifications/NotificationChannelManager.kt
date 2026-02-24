package com.jarvis.assistant.feature.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

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
) {
    URGENT("jarvis_urgent", "Jarvis Urgent", NotificationManager.IMPORTANCE_HIGH),
    IMPORTANT("jarvis_important", "Jarvis Important", NotificationManager.IMPORTANCE_DEFAULT),
    ROUTINE("jarvis_routine", "Jarvis Routine", NotificationManager.IMPORTANCE_LOW),
    BACKGROUND("jarvis_background", "Jarvis Background", NotificationManager.IMPORTANCE_MIN),
}

/**
 * Creates and manages the four Jarvis notification channels.
 *
 * Call [createChannels] once at app startup (idempotent -- Android ignores
 * duplicate channel creation). Use [classifyPriority] to map desktop alert
 * types to the correct channel.
 */
@Singleton
class NotificationChannelManager @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Create all four notification channels. Safe to call multiple times --
     * Android will not overwrite user-modified channel settings.
     */
    fun createChannels() {
        val channels = listOf(
            NotificationChannel(
                NotificationPriority.URGENT.channelId,
                NotificationPriority.URGENT.channelName,
                NotificationPriority.URGENT.importance,
            ).apply {
                description = "Critical alerts that bypass Do Not Disturb (medication, emergencies)"
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
                enableVibration(false)
                enableLights(false)
                setSound(null, null)
            },
        )

        channels.forEach { notificationManager.createNotificationChannel(it) }
        Log.i(TAG, "Created ${channels.size} notification channels")
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

    companion object {
        private const val TAG = "NotifChannelMgr"
    }
}
