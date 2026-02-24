package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Stores relationship context per contact -- last conversation date,
 * key topics, birthday/anniversary, and importance score.
 *
 * Pre-call cards use this to display context before a phone call.
 * Post-call logging updates this with new conversation notes.
 * RelationshipAlertEngine uses birthday, anniversary, and importance
 * for proactive social alerts.
 */
@Entity(
    tableName = "contact_context",
    indices = [Index(value = ["phoneNumber"], unique = true)],
)
data class ContactContextEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /** Normalized phone number (digits only, last 10 for matching). */
    val phoneNumber: String,
    /** Display name from contacts or user-entered. */
    val contactName: String,
    /** YYYY-MM-DD of last phone call. */
    val lastCallDate: String = "",
    /** Epoch millis of last call. */
    val lastCallTimestamp: Long = 0L,
    /** JSON array of topic strings from user's post-call notes. */
    val keyTopics: String = "[]",
    /** Free-text notes from most recent post-call log. */
    val lastNotes: String = "",
    /** MM-DD format (empty if unknown), sourced from desktop brain. */
    val birthday: String = "",
    /** MM-DD format (empty if unknown), sourced from desktop brain. */
    val anniversary: String = "",
    /** One of: family, friend, colleague, acquaintance, other. */
    val relationship: String = "other",
    /** Running count of calls with this contact. */
    val totalCalls: Int = 0,
    /** 0.0-1.0, derived from call frequency and recency. */
    val importance: Float = 0.0f,
    /** Whether context has been synced to desktop brain. */
    val syncedToDesktop: Boolean = false,
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
)
