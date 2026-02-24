package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Tracks user interactions with Jarvis notifications for priority learning.
 *
 * Each row records whether the user acted on, dismissed, or let a notification expire,
 * along with the time it took. The [NotificationLearner] uses this data to promote
 * or demote notification priority over a rolling 30-day window.
 */
@Entity(tableName = "notification_log")
data class NotificationLogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /** Android notification ID that was posted. */
    val notificationId: Int,
    /** Desktop alert type (e.g. "medication_reminder", "meeting_prep"). */
    val alertType: String,
    /** Human-readable notification title. */
    val title: String,
    /** Channel the notification was posted to (e.g. "jarvis_urgent"). */
    val channelId: String,
    /** One of "acted", "dismissed", "expired". */
    val action: String,
    /** Milliseconds between post time and user action (0 if expired). */
    val actionDelayMs: Long,
    val createdAt: Long = System.currentTimeMillis(),
)
