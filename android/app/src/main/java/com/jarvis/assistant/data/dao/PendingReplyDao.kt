package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.PendingReplyEntity

@Dao
interface PendingReplyDao {

    @Insert
    suspend fun insert(reply: PendingReplyEntity): Long

    @Query("SELECT * FROM pending_replies WHERE resolved = 0 ORDER BY receivedAt DESC")
    suspend fun getUnresolved(): List<PendingReplyEntity>

    @Query("UPDATE pending_replies SET resolved = 1 WHERE id = :id")
    suspend fun markResolved(id: Long)

    @Query("UPDATE pending_replies SET resolved = 1 WHERE contactName = :contactName AND packageName = :packageName")
    suspend fun resolveByContact(contactName: String, packageName: String)

    @Query("DELETE FROM pending_replies WHERE receivedAt < :beforeMs")
    suspend fun deleteOlderThan(beforeMs: Long)
}
