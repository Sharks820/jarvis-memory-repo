package com.jarvis.assistant.feature.scheduling

import android.content.ContentValues
import android.content.Context
import android.provider.CalendarContract
import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.entity.ExtractedEventEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Creates calendar events from extracted scheduling cues via CalendarProvider
 * and notifies the desktop engine for schedule conflict checking.
 */
@Singleton
class CalendarEventCreator @Inject constructor(
    @ApplicationContext private val context: Context,
    private val extractedEventDao: ExtractedEventDao,
    private val apiClient: JarvisApiClient,
) {

    companion object {
        private const val TAG = "CalendarEventCreator"
        private const val DEFAULT_DURATION_MS = 3_600_000L // 1 hour
    }

    /**
     * Create a calendar event from [cue] via CalendarProvider.
     *
     * - Checks for duplicate using SHA-256 content hash
     * - Inserts into Room DB first for tracking
     * - Inserts into device calendar via ContentResolver
     * - Returns the CalendarProvider event ID, or -1 on failure
     */
    suspend fun createEvent(cue: SchedulingCue): Long {
        val contentHash = SchedulingCueExtractor.contentHash(cue.sourceText)

        // Check if already processed
        val existing = extractedEventDao.findByHash(contentHash)
        if (existing != null && existing.calendarEventId > 0) {
            Log.d(TAG, "Event already created: ${existing.calendarEventId}")
            return existing.calendarEventId
        }

        val endMs = if (cue.endDateTime > 0) {
            cue.endDateTime
        } else if (cue.dateTime > 0) {
            cue.dateTime + DEFAULT_DURATION_MS
        } else {
            0L
        }

        // Insert tracking record into Room
        val entity = ExtractedEventEntity(
            contentHash = contentHash,
            title = cue.title,
            dateTimeMs = cue.dateTime,
            endDateTimeMs = endMs,
            location = cue.location,
            sourcePackage = cue.sourcePackage,
        )
        extractedEventDao.insertIfNew(entity)

        // Find writable calendar
        val calendarId = getWritableCalendarId()
        if (calendarId < 0) {
            Log.w(TAG, "No writable calendar found")
            return -1
        }

        // Insert into CalendarProvider
        return try {
            val values = ContentValues().apply {
                put(CalendarContract.Events.DTSTART, cue.dateTime)
                put(CalendarContract.Events.DTEND, endMs)
                put(CalendarContract.Events.TITLE, cue.title)
                put(CalendarContract.Events.EVENT_LOCATION, cue.location)
                put(CalendarContract.Events.CALENDAR_ID, calendarId)
                put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
                put(
                    CalendarContract.Events.DESCRIPTION,
                    "Auto-created by Jarvis from ${cue.sourcePackage} notification",
                )
            }

            val uri = context.contentResolver.insert(
                CalendarContract.Events.CONTENT_URI,
                values,
            )

            val eventId = uri?.lastPathSegment?.toLongOrNull() ?: -1L
            if (eventId > 0) {
                extractedEventDao.updateCalendarEventId(contentHash, eventId)
                Log.d(TAG, "Created calendar event $eventId: ${cue.title}")
            }
            eventId
        } catch (e: SecurityException) {
            Log.w(TAG, "Calendar permission denied: ${e.message}")
            -1
        } catch (e: Exception) {
            Log.e(TAG, "Failed to create calendar event: ${e.message}")
            -1
        }
    }

    /**
     * Notify the desktop engine about a new event for conflict checking.
     *
     * Sends a command to the desktop /command endpoint and parses the response
     * for conflict indicators ("conflict", "overlap", "busy").
     *
     * @return true if a conflict was detected
     */
    suspend fun notifyDesktopOfEvent(cue: SchedulingCue): Boolean {
        val contentHash = SchedulingCueExtractor.contentHash(cue.sourceText)
        val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US)
        val formattedDate = dateFormat.format(Date(cue.dateTime))

        return try {
            val response = apiClient.api().sendCommand(
                CommandRequest(
                    text = "Jarvis, check calendar conflict for ${cue.title} on $formattedDate",
                    execute = false,
                ),
            )

            val conflictDetected = response.stdoutTail.any { line ->
                val lower = line.lowercase(Locale.US)
                lower.contains("conflict") || lower.contains("overlap") || lower.contains("busy")
            }

            extractedEventDao.markDesktopNotified(contentHash, conflictDetected)
            Log.d(TAG, "Desktop notified for ${cue.title}, conflict=$conflictDetected")
            conflictDetected
        } catch (e: Exception) {
            Log.w(TAG, "Failed to notify desktop: ${e.message}")
            false
        }
    }

    /**
     * Query CalendarProvider for the first writable calendar.
     * Returns calendar ID or -1 if none found.
     */
    private fun getWritableCalendarId(): Long {
        val projection = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL,
        )
        val selection = "${CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL} >= ?"
        val selectionArgs = arrayOf(
            CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR.toString(),
        )

        return try {
            context.contentResolver.query(
                CalendarContract.Calendars.CONTENT_URI,
                projection,
                selection,
                selectionArgs,
                null,
            )?.use { cursor ->
                if (cursor.moveToFirst()) {
                    cursor.getLong(cursor.getColumnIndexOrThrow(CalendarContract.Calendars._ID))
                } else {
                    -1L
                }
            } ?: -1L
        } catch (e: SecurityException) {
            Log.w(TAG, "Calendar read permission denied: ${e.message}")
            -1L
        }
    }
}
