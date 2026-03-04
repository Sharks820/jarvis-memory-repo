package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.RecurringPatternEntity

@Dao
interface RecurringPatternDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(pattern: RecurringPatternEntity): Long

    @Query("SELECT * FROM recurring_patterns WHERE isActive = 1 ORDER BY lastSeen DESC")
    suspend fun getActive(): List<RecurringPatternEntity>

    @Query("SELECT * FROM recurring_patterns WHERE merchant = :merchant AND direction = :direction LIMIT 1")
    suspend fun findByMerchant(merchant: String, direction: String = "debit"): RecurringPatternEntity?

    @Query("SELECT SUM(normalizedAmount) FROM recurring_patterns WHERE isActive = 1 AND direction = 'debit' AND period = 'MONTHLY'")
    suspend fun getMonthlySubscriptionTotal(): Double?

    @Update
    suspend fun update(pattern: RecurringPatternEntity)
}
