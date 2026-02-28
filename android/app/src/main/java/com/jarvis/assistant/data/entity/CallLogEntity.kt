package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Records individual call interactions with context notes.
 *
 * Each phone call produces one entry with user's post-call notes
 * about what was discussed and extracted topic keywords.
 */
@Entity(
    tableName = "call_interaction_log",
    indices = [Index(value = ["contactContextId"])],
)
data class CallLogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /** Foreign key reference to contact_context.id. */
    val contactContextId: Long,
    /** The number called/received. */
    val phoneNumber: String,
    /** Denormalized contact name. */
    val contactName: String,
    /** "incoming" or "outgoing". */
    val direction: String,
    /** Call duration in seconds (0 if unknown). */
    val durationSeconds: Int = 0,
    /** User's post-call notes about what was discussed. */
    val notes: String = "",
    /** JSON array of extracted topic keywords from the notes. */
    val topics: String = "[]",
    /** When the call happened (epoch millis). */
    val timestamp: Long,
    /** YYYY-MM-DD date string. */
    val date: String,
)
