package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.SleepSessionEntity

@Dao
interface SleepSessionDao {

    @Insert
    suspend fun insert(session: SleepSessionEntity): Long

    @Query("SELECT * FROM sleep_sessions WHERE date BETWEEN :startDate AND :endDate ORDER BY date DESC")
    suspend fun getInRange(startDate: String, endDate: String): List<SleepSessionEntity>

    @Query("SELECT AVG(durationMinutes) FROM sleep_sessions WHERE date >= :sinceDate")
    suspend fun getAvgDuration(sinceDate: String): Double?

    @Query("SELECT AVG(qualityScore) FROM sleep_sessions WHERE date >= :sinceDate")
    suspend fun getAvgQuality(sinceDate: String): Double?

    @Query("SELECT * FROM sleep_sessions ORDER BY date DESC LIMIT 1")
    suspend fun getLatest(): SleepSessionEntity?
}
