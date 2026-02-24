package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Logs detected context states (meeting, driving, sleeping, gaming, normal)
 * for historical tracking and Settings display.
 */
@Entity(tableName = "context_state_log")
data class ContextStateEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /** [UserContext] enum name (e.g. "MEETING", "DRIVING"). */
    val context: String,
    /** Detection confidence 0.0 - 1.0. */
    val confidence: Float,
    /** Detection source: "calendar", "accelerometer", "time", "gaming_sync", "manual". */
    val source: String,
    val createdAt: Long = System.currentTimeMillis(),
)
