package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.HabitPatternEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface HabitDao {

    @Query(
        "SELECT * FROM habit_patterns WHERE isActive = 1 AND isSuppressed = 0 " +
            "ORDER BY confidence DESC",
    )
    fun getActivePatternsFlow(): Flow<List<HabitPatternEntity>>

    @Query("SELECT * FROM habit_patterns WHERE isActive = 1 AND isSuppressed = 0")
    suspend fun getActivePatterns(): List<HabitPatternEntity>

    @Query("SELECT * FROM habit_patterns WHERE isActive = 1")
    fun getAllActivePatternsFlow(): Flow<List<HabitPatternEntity>>

    @Query(
        "SELECT * FROM habit_patterns WHERE patternType = :type AND label = :label LIMIT 1",
    )
    suspend fun findByTypeAndLabel(type: String, label: String): HabitPatternEntity?

    @Query(
        "SELECT * FROM habit_patterns WHERE patternType = :type AND label LIKE :labelPrefix || '%'",
    )
    suspend fun findByTypeAndLabelPrefix(type: String, labelPrefix: String): List<HabitPatternEntity>

    @Query("SELECT * FROM habit_patterns WHERE id = :id")
    suspend fun getById(id: Long): HabitPatternEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(pattern: HabitPatternEntity): Long

    @Update
    suspend fun update(pattern: HabitPatternEntity)

    @Query(
        "UPDATE habit_patterns SET isSuppressed = 1, updatedAt = :now WHERE id = :id",
    )
    suspend fun suppress(id: Long, now: Long = System.currentTimeMillis())

    @Query(
        "UPDATE habit_patterns SET isActive = 0, updatedAt = :now WHERE id = :id",
    )
    suspend fun deactivate(id: Long, now: Long = System.currentTimeMillis())

    @Query(
        "UPDATE habit_patterns SET occurrenceCount = occurrenceCount + 1, " +
            "confidence = :newConfidence, updatedAt = :now WHERE id = :id",
    )
    suspend fun incrementOccurrence(
        id: Long,
        newConfidence: Float,
        now: Long = System.currentTimeMillis(),
    )

    @Query("SELECT COUNT(*) FROM habit_patterns WHERE isActive = 1")
    fun activeCountFlow(): Flow<Int>

    @Query("SELECT * FROM habit_patterns WHERE isSuppressed = 1")
    suspend fun getSuppressedPatterns(): List<HabitPatternEntity>

    @Query("UPDATE habit_patterns SET isSuppressed = 0, updatedAt = :now WHERE isSuppressed = 1")
    suspend fun unsuppressAll(now: Long = System.currentTimeMillis())

    @Query(
        "UPDATE habit_patterns SET isSuppressed = 0, updatedAt = :now WHERE id = :id",
    )
    suspend fun unsuppress(id: Long, now: Long = System.currentTimeMillis())

    @Query(
        "UPDATE habit_patterns SET isActive = 1, updatedAt = :now WHERE id = :id",
    )
    suspend fun activate(id: Long, now: Long = System.currentTimeMillis())

    @Query("SELECT * FROM habit_patterns WHERE isActive = 1")
    suspend fun getAllActivePatterns(): List<HabitPatternEntity>
}
