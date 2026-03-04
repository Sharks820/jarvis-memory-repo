package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Tracks incoming messages that haven't been replied to yet.
 *
 * [importance] is derived from [ContactContextEntity.importance] — higher
 * values trigger faster reminders. [resolved] is set when an outbound
 * message to the same contact is detected.
 */
@Entity(tableName = "pending_replies")
data class PendingReplyEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val contactName: String,
    val phoneNumber: String = "",
    val packageName: String,
    val messagePreview: String,
    val receivedAt: Long = System.currentTimeMillis(),
    val importance: Float = 0.5f,
    val reminderSentAt: Long? = null,
    val resolved: Boolean = false,
)
