package com.jarvis.assistant.data.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entity for tracking calendar events extracted from notifications.
 *
 * Uses SHA-256 [contentHash] of the source text as primary key to prevent
 * duplicate event creation when the same notification is processed multiple times.
 */
@Entity(tableName = "extracted_events")
data class ExtractedEventEntity(
    @PrimaryKey
    @ColumnInfo(name = "content_hash")
    val contentHash: String,

    val title: String,

    @ColumnInfo(name = "date_time_ms")
    val dateTimeMs: Long,

    @ColumnInfo(name = "end_date_time_ms")
    val endDateTimeMs: Long,

    val location: String,

    @ColumnInfo(name = "source_package")
    val sourcePackage: String,

    /** CalendarProvider event ID. 0 if not yet created. */
    @ColumnInfo(name = "calendar_event_id")
    val calendarEventId: Long = 0,

    @ColumnInfo(name = "desktop_notified")
    val desktopNotified: Boolean = false,

    @ColumnInfo(name = "conflict_detected")
    val conflictDetected: Boolean = false,

    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),
)
