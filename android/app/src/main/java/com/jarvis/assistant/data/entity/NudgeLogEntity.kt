package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Room entity for nudge delivery and response tracking.
 *
 * Each entry records a nudge that was shown to the user, along with their
 * response (acted, dismissed, or expired). The response rate per pattern
 * drives adaptive suppression via [NudgeResponseTracker].
 */
@Entity(
    tableName = "nudge_log",
    indices = [Index(value = ["patternId"])],
)
data class NudgeLogEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    /** Foreign key reference to habit_patterns.id. */
    val patternId: Long,
    /** Denormalized for easy querying. */
    val patternLabel: String,
    /** The actual text shown in the notification. */
    val nudgeText: String,
    /** Timestamp when nudge was posted. */
    val deliveredAt: Long,
    /** Timestamp when user interacted (0L if dismissed/ignored). */
    val respondedAt: Long = 0L,
    /** One of "acted", "dismissed", "expired", or "" (pending). */
    val response: String = "",
    /** YYYY-MM-DD for easy date queries. */
    val date: String,
)
