package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.CallLogEntity
import kotlinx.coroutines.flow.Flow

/**
 * Room DAO for call interaction log queries.
 *
 * Supports per-contact history, date-based lookups, and recent log retrieval.
 */
@Dao
interface CallLogDao {

    @Insert
    suspend fun insert(log: CallLogEntity): Long

    @Update
    suspend fun update(log: CallLogEntity)

    @Query(
        "SELECT * FROM call_interaction_log WHERE contactContextId = :contactId " +
            "ORDER BY timestamp DESC LIMIT :limit",
    )
    suspend fun getLogsForContact(contactId: Long, limit: Int = 10): List<CallLogEntity>

    @Query("SELECT * FROM call_interaction_log WHERE date = :date ORDER BY timestamp DESC")
    suspend fun getLogsForDate(date: String): List<CallLogEntity>

    @Query("SELECT * FROM call_interaction_log ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getRecentLogs(limit: Int = 20): List<CallLogEntity>

    @Query("SELECT COUNT(*) FROM call_interaction_log")
    fun totalCountFlow(): Flow<Int>

    @Query("SELECT * FROM call_interaction_log WHERE id = :id")
    suspend fun getById(id: Long): CallLogEntity?
}
