package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.NotificationLogEntity
import kotlinx.coroutines.flow.Flow

/**
 * Summary of act/dismiss counts grouped by alert type and action.
 */
data class AlertActionCount(
    val alertType: String,
    val action: String,
    val cnt: Int,
)

@Dao
interface NotificationLogDao {

    @Insert
    suspend fun insert(log: NotificationLogEntity)

    /**
     * Returns act/dismiss/expired counts per alert type since the given timestamp.
     * Used by [NotificationLearner] to calculate dismiss rates.
     */
    @Query(
        """
        SELECT alertType, action, COUNT(*) as cnt
        FROM notification_log
        WHERE createdAt > :since
        GROUP BY alertType, action
        """,
    )
    suspend fun getActionCounts(since: Long): List<AlertActionCount>

    /** Total notifications tracked (displayed in Settings). */
    @Query("SELECT COUNT(*) FROM notification_log")
    fun totalCountFlow(): Flow<Int>

    /** Delete all entries (used by "Reset Learning Data" button). */
    @Query("DELETE FROM notification_log")
    suspend fun deleteAll()
}
