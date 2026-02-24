package com.jarvis.assistant.data.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entity representing a spam number candidate synced from the desktop
 * phone_guard module.
 */
@Entity(tableName = "spam_numbers")
data class SpamEntity(
    @PrimaryKey
    val number: String,

    val score: Float,

    val calls: Int,

    @ColumnInfo(name = "missed_ratio")
    val missedRatio: Float,

    @ColumnInfo(name = "avg_duration_s")
    val avgDurationS: Float,

    /** JSON-encoded list of reason strings (e.g. ["high_repeat_volume","burst_day_pattern"]). */
    val reasons: String,

    @ColumnInfo(name = "last_synced")
    val lastSynced: Long,

    /**
     * User-configured action for this number.
     * One of: "block", "silence", "voicemail", "allow", "auto".
     * "auto" means the system decides based on the score and threshold settings.
     */
    @ColumnInfo(name = "user_action", defaultValue = "auto")
    val userAction: String = "auto",
)
