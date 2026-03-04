package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Passively-detected errand or shopping list item.
 *
 * Populated from conversation cues ("I need to grab dog food"),
 * voice commands, or notification parsing (e.g., "prescription ready").
 * [source] is one of: "conversation", "notification", "voice".
 */
@Entity(tableName = "errands")
data class ErrandEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val item: String,
    val store: String = "",
    val storeLatitude: Double? = null,
    val storeLongitude: Double? = null,
    val source: String = "conversation",
    val confidence: Float = 0.8f,
    val createdAt: Long = System.currentTimeMillis(),
    val completedAt: Long? = null,
    val snoozedUntil: Long? = null,
)
