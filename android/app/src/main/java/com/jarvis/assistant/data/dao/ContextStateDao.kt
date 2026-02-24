package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.ContextStateEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface ContextStateDao {

    @Insert
    suspend fun insert(state: ContextStateEntity)

    /** Get the most recently detected context state. */
    @Query("SELECT * FROM context_state_log ORDER BY createdAt DESC LIMIT 1")
    suspend fun getLatest(): ContextStateEntity?

    /** Observe the 50 most recent context state changes. */
    @Query("SELECT * FROM context_state_log ORDER BY createdAt DESC LIMIT 50")
    fun recentFlow(): Flow<List<ContextStateEntity>>

    /** Delete old entries to prevent unbounded growth. */
    @Query("DELETE FROM context_state_log WHERE createdAt < :cutoff")
    suspend fun deleteOld(cutoff: Long)
}
