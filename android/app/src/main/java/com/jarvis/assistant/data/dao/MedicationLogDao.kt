package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import com.jarvis.assistant.data.entity.MedicationLogEntity

@Dao
interface MedicationLogDao {

    @Insert
    suspend fun insert(log: MedicationLogEntity): Long

    @Query("SELECT * FROM medication_log WHERE date = :date ORDER BY scheduledTime")
    suspend fun getLogsForDate(date: String): List<MedicationLogEntity>

    @Query("SELECT * FROM medication_log WHERE medicationId = :medId AND date = :date")
    suspend fun getLogsForMedicationOnDate(medId: Long, date: String): List<MedicationLogEntity>

    @Query("SELECT * FROM medication_log WHERE date = :date AND status = 'taken'")
    suspend fun getTakenLogsForDate(date: String): List<MedicationLogEntity>

    @Query(
        "UPDATE medication_log SET status = 'missed' " +
            "WHERE date = :date AND status != 'taken' AND scheduledTime < :cutoffTime",
    )
    suspend fun markMissedDoses(date: String, cutoffTime: String)
}
