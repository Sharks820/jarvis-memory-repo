package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.MedicationEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface MedicationDao {

    @Query("SELECT * FROM medications WHERE isActive = 1 ORDER BY name")
    fun getActiveMedicationsFlow(): Flow<List<MedicationEntity>>

    @Query("SELECT * FROM medications WHERE isActive = 1")
    suspend fun getActiveMedications(): List<MedicationEntity>

    @Query("SELECT * FROM medications WHERE id = :id")
    suspend fun getById(id: Long): MedicationEntity?

    @Insert
    suspend fun insert(medication: MedicationEntity): Long

    @Update
    suspend fun update(medication: MedicationEntity)

    @Query(
        "UPDATE medications SET pillsRemaining = pillsRemaining - 1, updatedAt = :now " +
            "WHERE id = :id AND pillsRemaining > 0",
    )
    suspend fun decrementPills(id: Long, now: Long = System.currentTimeMillis())

    @Query("UPDATE medications SET isActive = 0 WHERE id = :id")
    suspend fun deactivate(id: Long)
}
