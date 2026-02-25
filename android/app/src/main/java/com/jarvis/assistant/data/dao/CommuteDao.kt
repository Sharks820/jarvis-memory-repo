package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Update
import androidx.room.Upsert
import com.jarvis.assistant.data.entity.CommuteLocationEntity
import com.jarvis.assistant.data.entity.ParkingEntity
import kotlinx.coroutines.flow.Flow

/**
 * Room DAO for commute locations and parking memory.
 *
 * Handles both the learned home/work/frequent locations and the
 * Bluetooth-triggered parking GPS entries.
 */
@Dao
interface CommuteDao {

    // ── Commute Locations ──────────────────────────────────────

    @Insert
    suspend fun insertLocation(location: CommuteLocationEntity): Long

    @Update
    suspend fun updateLocation(location: CommuteLocationEntity)

    @Upsert
    suspend fun upsertLocation(location: CommuteLocationEntity)

    @Query(
        "SELECT * FROM commute_locations WHERE label = :label " +
            "ORDER BY confidence DESC LIMIT 1",
    )
    suspend fun getLocationByLabel(label: String): CommuteLocationEntity?

    @Query("SELECT * FROM commute_locations ORDER BY lastVisited DESC")
    fun getAllLocationsFlow(): Flow<List<CommuteLocationEntity>>

    @Query("SELECT * FROM commute_locations ORDER BY lastVisited DESC")
    suspend fun getAllLocations(): List<CommuteLocationEntity>

    // ── Parking ─────────────────────────────────────────────────

    @Insert
    suspend fun insertParking(parking: ParkingEntity): Long

    @Query("UPDATE parking_locations SET isActive = 0")
    suspend fun deactivateAllParking()

    @Query(
        "SELECT * FROM parking_locations WHERE isActive = 1 " +
            "ORDER BY timestamp DESC LIMIT 1",
    )
    suspend fun getActiveParking(): ParkingEntity?

    @Query("SELECT * FROM parking_locations WHERE isActive = 1")
    fun getActiveParkingFlow(): Flow<ParkingEntity?>

    /**
     * Atomically deactivates all existing parking entries and inserts a new one.
     * Ensures no window where zero or multiple entries are active.
     */
    @Transaction
    suspend fun replaceActiveParking(parking: ParkingEntity): Long {
        deactivateAllParking()
        return insertParking(parking)
    }
}
