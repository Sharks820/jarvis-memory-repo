package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Logged commute or trip for travel-time anomaly detection.
 *
 * Used by the Departure Oracle to compare today's travel time against
 * the 7-day rolling average for the same route.
 */
@Entity(tableName = "commute_log")
data class CommuteLogEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val date: String,
    val originLabel: String = "",
    val destinationLabel: String = "",
    val durationMinutes: Int,
    val distanceKm: Float = 0f,
    val timestamp: Long = System.currentTimeMillis(),
)
