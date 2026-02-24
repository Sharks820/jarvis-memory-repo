package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entity for learned locations (home/work/frequent).
 *
 * Locations are learned automatically by [LocationLearner] from GPS patterns.
 * After 5 visits a "frequent" location is auto-classified as "home" or "work"
 * based on time-of-day distribution. [confidence] increases with [visitCount]
 * up to a maximum of 1.0 at 20 visits.
 */
@Entity(tableName = "commute_locations")
data class CommuteLocationEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val label: String,
    val latitude: Double,
    val longitude: Double,
    val radius: Float = 200f,
    val visitCount: Int = 1,
    val avgArrivalHour: Float = 0f,
    val avgDepartureHour: Float = 0f,
    val lastVisited: Long = System.currentTimeMillis(),
    val confidence: Float = 0.05f,
)
