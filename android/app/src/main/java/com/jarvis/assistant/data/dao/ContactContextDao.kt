package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Update
import androidx.room.Upsert
import com.jarvis.assistant.data.entity.ContactContextEntity
import kotlinx.coroutines.flow.Flow

/**
 * Room DAO for contact context CRUD.
 *
 * Supports pre-call lookup by phone number, neglected contact detection,
 * and birthday/anniversary filtering for relationship alerts.
 */
@Dao
interface ContactContextDao {

    @Query("SELECT * FROM contact_context WHERE phoneNumber = :number LIMIT 1")
    suspend fun getByPhoneNumber(number: String): ContactContextEntity?

    @Query("SELECT * FROM contact_context ORDER BY lastCallTimestamp DESC")
    fun getAllFlow(): Flow<List<ContactContextEntity>>

    @Query("SELECT * FROM contact_context ORDER BY lastCallTimestamp DESC")
    suspend fun getAll(): List<ContactContextEntity>

    @Query(
        "SELECT * FROM contact_context WHERE lastCallTimestamp < :cutoff " +
            "AND importance > 0.3 ORDER BY importance DESC",
    )
    suspend fun getNeglectedContacts(cutoff: Long): List<ContactContextEntity>

    @Query("SELECT * FROM contact_context WHERE birthday != '' ORDER BY birthday")
    suspend fun getContactsWithBirthdays(): List<ContactContextEntity>

    @Query("SELECT * FROM contact_context WHERE anniversary != '' ORDER BY anniversary")
    suspend fun getContactsWithAnniversaries(): List<ContactContextEntity>

    @Upsert
    suspend fun upsert(contact: ContactContextEntity): Long

    @Update
    suspend fun update(contact: ContactContextEntity)

    @Query("SELECT COUNT(*) FROM contact_context")
    fun countFlow(): Flow<Int>

    @Query("SELECT * FROM contact_context WHERE syncedToDesktop = 0")
    suspend fun getUnsyncedContacts(): List<ContactContextEntity>

    @Query("UPDATE contact_context SET syncedToDesktop = 1 WHERE id = :id")
    suspend fun markSynced(id: Long)
}
