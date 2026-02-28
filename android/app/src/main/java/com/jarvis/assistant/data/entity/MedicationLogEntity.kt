package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Room entity for tracking individual dose-taken / skipped / missed events.
 *
 * [status] is one of: "taken", "skipped", "missed".
 * [date] is formatted as YYYY-MM-DD for easy date-based queries.
 */
@Entity(
    tableName = "medication_log",
    indices = [Index(value = ["medicationId"])],
)
data class MedicationLogEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val medicationId: Long,
    /** Denormalized for easy querying without joins. */
    val medicationName: String,
    /** HH:mm time this dose was scheduled for. */
    val scheduledTime: String,
    /** Epoch millis when user confirmed dose, 0L if skipped/missed. */
    val takenAt: Long = 0L,
    /** One of "taken", "skipped", "missed". */
    val status: String,
    /** YYYY-MM-DD format. */
    val date: String,
)
