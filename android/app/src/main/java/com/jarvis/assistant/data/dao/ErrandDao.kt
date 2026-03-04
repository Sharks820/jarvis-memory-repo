package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.ErrandEntity

@Dao
interface ErrandDao {

    @Insert
    suspend fun insert(errand: ErrandEntity): Long

    @Query("SELECT * FROM errands WHERE completedAt IS NULL AND (snoozedUntil IS NULL OR snoozedUntil < :nowMs) ORDER BY createdAt DESC")
    suspend fun getPending(nowMs: Long = System.currentTimeMillis()): List<ErrandEntity>

    @Query("SELECT * FROM errands WHERE completedAt IS NULL AND store = :store")
    suspend fun getPendingForStore(store: String): List<ErrandEntity>

    @Query("UPDATE errands SET completedAt = :nowMs WHERE id = :id")
    suspend fun markCompleted(id: Long, nowMs: Long = System.currentTimeMillis())

    @Query("UPDATE errands SET snoozedUntil = :until WHERE id = :id")
    suspend fun snooze(id: Long, until: Long)
}
