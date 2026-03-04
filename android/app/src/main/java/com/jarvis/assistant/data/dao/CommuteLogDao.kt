package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.CommuteLogEntity

@Dao
interface CommuteLogDao {

    @Insert
    suspend fun insert(log: CommuteLogEntity): Long

    @Query("SELECT AVG(durationMinutes) FROM commute_log WHERE date >= :sinceDate")
    suspend fun getAvgDuration(sinceDate: String): Double?

    @Query("SELECT * FROM commute_log WHERE date BETWEEN :startDate AND :endDate ORDER BY date DESC")
    suspend fun getInRange(startDate: String, endDate: String): List<CommuteLogEntity>
}
