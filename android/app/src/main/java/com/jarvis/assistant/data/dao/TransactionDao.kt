package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.TransactionEntity
import kotlinx.coroutines.flow.Flow

/**
 * Room DAO for transaction CRUD and aggregation queries.
 *
 * [insert] uses [OnConflictStrategy.IGNORE] so duplicate notifications
 * (same [TransactionEntity.notificationHash]) are silently skipped.
 */
@Dao
interface TransactionDao {

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(transaction: TransactionEntity): Long

    @Update
    suspend fun update(transaction: TransactionEntity)

    @Query(
        "SELECT * FROM transactions WHERE date BETWEEN :startDate AND :endDate " +
            "ORDER BY timestamp DESC",
    )
    suspend fun getTransactionsInRange(startDate: String, endDate: String): List<TransactionEntity>

    @Query("SELECT * FROM transactions ORDER BY timestamp DESC LIMIT :limit")
    fun getRecentFlow(limit: Int = 50): Flow<List<TransactionEntity>>

    @Query(
        "SELECT SUM(amount) FROM transactions " +
            "WHERE date BETWEEN :startDate AND :endDate AND category != 'refund'",
    )
    suspend fun getTotalSpendInRange(startDate: String, endDate: String): Double?

    @Query(
        "SELECT merchant, COUNT(*) as count, AVG(amount) as avgAmount " +
            "FROM transactions WHERE merchant = :merchant GROUP BY merchant",
    )
    suspend fun getMerchantStats(merchant: String): MerchantStats?

    @Query(
        "SELECT AVG(amount) FROM transactions " +
            "WHERE category = :category AND date >= :sinceDate",
    )
    suspend fun getAverageAmountForCategory(category: String, sinceDate: String): Double?

    @Query(
        "SELECT COUNT(*) FROM transactions " +
            "WHERE date BETWEEN :startDate AND :endDate AND isAnomaly = 1",
    )
    suspend fun getAnomalyCountInRange(startDate: String, endDate: String): Int
}

/** Aggregate stats for a single merchant. */
data class MerchantStats(
    val merchant: String,
    val count: Int,
    val avgAmount: Double,
)
