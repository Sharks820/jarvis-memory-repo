package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Estimated sleep session derived from screen on/off patterns.
 *
 * [qualityScore] ranges from 0-100; higher is better.
 * [date] is the wake-up date (yyyy-MM-dd) since sleep spans midnight.
 */
@Entity(tableName = "sleep_sessions")
data class SleepSessionEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val onsetTime: Long,
    val wakeTime: Long,
    val durationMinutes: Int,
    val qualityScore: Int,
    val interruptions: Int = 0,
    val restlessPeriods: Int = 0,
    val date: String,
    val createdAt: Long = System.currentTimeMillis(),
)
