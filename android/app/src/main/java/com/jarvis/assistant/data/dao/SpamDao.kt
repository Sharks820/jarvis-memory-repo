package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import com.jarvis.assistant.data.entity.SpamEntity
import kotlinx.coroutines.flow.Flow

/** Room DAO for spam number candidate records. */
@Dao
interface SpamDao {

    @Query("SELECT * FROM spam_numbers WHERE number = :number")
    suspend fun findByNumber(number: String): SpamEntity?

    @Query("SELECT * FROM spam_numbers ORDER BY score DESC")
    fun getAllFlow(): Flow<List<SpamEntity>>

    @Upsert
    suspend fun upsertAll(entries: List<SpamEntity>)

    @Query("DELETE FROM spam_numbers WHERE last_synced < :cutoff")
    suspend fun deleteStale(cutoff: Long)
}
