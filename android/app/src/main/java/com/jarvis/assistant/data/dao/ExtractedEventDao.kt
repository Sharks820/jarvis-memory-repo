package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.jarvis.assistant.data.entity.ExtractedEventEntity
import kotlinx.coroutines.flow.Flow

/** Room DAO for tracking extracted calendar events from notifications. */
@Dao
interface ExtractedEventDao {

    @Query("SELECT * FROM extracted_events WHERE content_hash = :hash")
    suspend fun findByHash(hash: String): ExtractedEventEntity?

    /**
     * Insert a new event record, ignoring if the content hash already exists.
     * Returns the rowId on success, or -1 if the record was already present.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertIfNew(event: ExtractedEventEntity): Long

    @Query("UPDATE extracted_events SET calendar_event_id = :eventId WHERE content_hash = :hash")
    suspend fun updateCalendarEventId(hash: String, eventId: Long)

    @Query("UPDATE extracted_events SET desktop_notified = 1, conflict_detected = :conflict WHERE content_hash = :hash")
    suspend fun markDesktopNotified(hash: String, conflict: Boolean)

    @Query("SELECT * FROM extracted_events ORDER BY created_at DESC LIMIT 50")
    fun recentFlow(): Flow<List<ExtractedEventEntity>>

    @Query("SELECT COUNT(*) FROM extracted_events")
    fun countFlow(): Flow<Int>

    @Query("DELETE FROM extracted_events WHERE content_hash = :hash")
    suspend fun deleteByHash(hash: String)

    /** Get upcoming events in a time range (for on-device intelligence context). */
    @Query(
        "SELECT * FROM extracted_events " +
            "WHERE date_time_ms >= :fromMs AND date_time_ms <= :toMs " +
            "ORDER BY date_time_ms ASC",
    )
    suspend fun getUpcomingEvents(fromMs: Long, toMs: Long): List<ExtractedEventEntity>
}
