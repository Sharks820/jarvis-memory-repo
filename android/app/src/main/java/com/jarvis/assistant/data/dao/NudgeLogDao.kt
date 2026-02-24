package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.NudgeLogEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface NudgeLogDao {

    @Insert
    suspend fun insert(log: NudgeLogEntity): Long

    @Query(
        "SELECT * FROM nudge_log WHERE patternId = :patternId " +
            "ORDER BY deliveredAt DESC LIMIT :limit",
    )
    suspend fun getLogsForPattern(patternId: Long, limit: Int = 20): List<NudgeLogEntity>

    @Query("SELECT * FROM nudge_log WHERE date = :date ORDER BY deliveredAt DESC")
    suspend fun getLogsForDate(date: String): List<NudgeLogEntity>

    @Query(
        "SELECT COUNT(*) FROM nudge_log WHERE patternId = :patternId AND response = 'acted'",
    )
    suspend fun getActedCount(patternId: Long): Int

    @Query(
        "SELECT COUNT(*) FROM nudge_log WHERE patternId = :patternId " +
            "AND response IN ('dismissed', 'expired')",
    )
    suspend fun getIgnoredCount(patternId: Long): Int

    @Query("SELECT COUNT(*) FROM nudge_log WHERE patternId = :patternId")
    suspend fun getTotalCount(patternId: Long): Int

    @Query(
        "UPDATE nudge_log SET response = :response, respondedAt = :respondedAt WHERE id = :id",
    )
    suspend fun updateResponse(
        id: Long,
        response: String,
        respondedAt: Long = System.currentTimeMillis(),
    )

    @Query(
        "UPDATE nudge_log SET response = 'expired' " +
            "WHERE response = '' AND deliveredAt < :cutoff",
    )
    suspend fun expireOldNudges(cutoff: Long)

    @Query("SELECT COUNT(*) FROM nudge_log")
    fun totalCountFlow(): Flow<Int>
}
