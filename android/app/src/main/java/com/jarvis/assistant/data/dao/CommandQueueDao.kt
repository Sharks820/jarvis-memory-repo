package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.CommandQueueEntity

@Dao
interface CommandQueueDao {

    @Query("SELECT * FROM command_queue WHERE status = 'pending' ORDER BY created_at ASC")
    suspend fun getPending(): List<CommandQueueEntity>

    @Query("SELECT * FROM command_queue WHERE id = :id")
    suspend fun getById(id: Long): CommandQueueEntity?

    @Insert
    suspend fun insert(command: CommandQueueEntity): Long

    @Query("UPDATE command_queue SET status = :status, response = :response WHERE id = :id")
    suspend fun updateStatus(id: Long, status: String, response: String? = null)

    @Query("UPDATE command_queue SET retry_count = retry_count + 1 WHERE id = :id")
    suspend fun incrementRetry(id: Long)

    /** Atomically claim a pending command for sending. Returns rows affected (0 or 1). */
    @Query("UPDATE command_queue SET status = 'sending' WHERE id = :id AND status = 'pending'")
    suspend fun claimForSend(id: Long): Int

    /** Reset stale 'sending' commands back to 'pending' (crash recovery). */
    @Query("UPDATE command_queue SET status = 'pending' WHERE status = 'sending'")
    suspend fun recoverStaleSending(): Int

    @Query("DELETE FROM command_queue WHERE status = 'sent' AND created_at < :before")
    suspend fun purgeSent(before: Long)
}
