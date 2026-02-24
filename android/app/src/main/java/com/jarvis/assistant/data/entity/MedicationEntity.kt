package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entity representing a medication schedule.
 *
 * [scheduledTimes] stores a JSON array of HH:mm strings (e.g. ["08:00","20:00"]).
 * [frequency] is one of: daily, twice_daily, three_times_daily, weekly, as_needed.
 */
@Entity(tableName = "medications")
data class MedicationEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val name: String,
    val dosage: String,
    val frequency: String,
    /** JSON array of HH:mm strings, e.g. ["08:00","20:00"] */
    val scheduledTimes: String,
    val pillsRemaining: Int,
    val pillsPerRefill: Int = 30,
    val refillReminderDays: Int = 7,
    val isActive: Boolean = true,
    val notes: String = "",
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
)
