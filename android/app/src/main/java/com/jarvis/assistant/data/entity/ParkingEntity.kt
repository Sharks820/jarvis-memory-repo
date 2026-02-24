package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entity for saved parking locations.
 *
 * A new parking entry is created when a configured car Bluetooth device
 * disconnects ([ParkingMemory]). The most recent active entry represents
 * the current parking spot. Historical entries (isActive = false) are kept
 * for pattern analysis.
 */
@Entity(tableName = "parking_locations")
data class ParkingEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val latitude: Double,
    val longitude: Double,
    val accuracy: Float,
    val bluetoothDeviceName: String,
    val timestamp: Long = System.currentTimeMillis(),
    val isActive: Boolean = true,
    val note: String = "",
)
