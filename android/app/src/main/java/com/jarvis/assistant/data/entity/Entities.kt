package com.jarvis.assistant.data.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/** Chat conversation messages. */
@Entity(tableName = "conversations")
data class ConversationEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val role: String,            // "user" or "assistant"
    val content: String,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
)

/** Offline command queue for when desktop is unreachable. */
@Entity(tableName = "command_queue")
data class CommandQueueEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val text: String,
    val execute: Boolean = false,
    @ColumnInfo(name = "approve_privileged") val approvePrivileged: Boolean = false,
    val speak: Boolean = false,
    val status: String = "pending",  // pending, sent, failed
    @ColumnInfo(name = "retry_count") val retryCount: Int = 0,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "last_attempt_at") val lastAttemptAt: Long = 0L,
    @ColumnInfo(name = "response") val response: String? = null,
)
