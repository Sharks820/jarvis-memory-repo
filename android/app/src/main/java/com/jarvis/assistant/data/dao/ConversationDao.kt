package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.ConversationEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface ConversationDao {

    @Query("SELECT * FROM conversations ORDER BY created_at DESC LIMIT :limit OFFSET :offset")
    fun getMessages(limit: Int = 50, offset: Int = 0): Flow<List<ConversationEntity>>

    @Query("SELECT * FROM conversations ORDER BY created_at DESC LIMIT 1")
    suspend fun getLatestMessage(): ConversationEntity?

    @Insert
    suspend fun insert(message: ConversationEntity): Long

    @Query("DELETE FROM conversations")
    suspend fun deleteAll()

    @Query("SELECT COUNT(*) FROM conversations")
    suspend fun count(): Int

    /** Delete entries older than the given timestamp to prevent unbounded table growth. */
    @Query("DELETE FROM conversations WHERE created_at < :cutoff")
    suspend fun deleteOlderThan(cutoff: Long)
}
