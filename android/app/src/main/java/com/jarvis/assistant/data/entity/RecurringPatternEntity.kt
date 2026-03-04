package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Detected recurring transaction pattern (subscription, paycheck, etc.).
 *
 * [period] is one of: "WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY", "ANNUAL".
 * [direction] is "debit" (outgoing charge) or "credit" (incoming deposit).
 * [isGhost] flags charges for services whose app is no longer installed.
 */
@Entity(tableName = "recurring_patterns")
data class RecurringPatternEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val merchant: String,
    val normalizedAmount: Double,
    val period: String,
    val direction: String = "debit",
    val counterparty: String = "",
    val lastSeen: String,
    val firstSeen: String,
    val isActive: Boolean = true,
    val isGhost: Boolean = false,
    val occurrenceCount: Int = 2,
    val createdAt: Long = System.currentTimeMillis(),
)
