package com.jarvis.assistant.feature.automation

import android.app.NotificationManager
import android.content.ContentUris
import android.content.Context
import android.database.Cursor
import android.net.Uri
import android.provider.CalendarContract
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Pre-meeting intelligence briefing.
 *
 * Runs every 2 minutes from SyncWorker. Checks calendar for events
 * starting in 5-15 minutes. For qualifying events, queries the desktop
 * KG via GET /meeting-prep to build a contextual briefing card with:
 * - What Jarvis knows about each attendee (from KG)
 * - Recent conversation memories related to the topic
 * - Suggested discussion topics
 *
 * This eliminates the manual step of reviewing past notes before meetings.
 */
@Singleton
class MeetingPrepService @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiClient: JarvisApiClient,
) {

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /** Track which events we've already briefed to avoid duplicates. */
    private val briefedEventIds = mutableSetOf<Long>()

    /**
     * Check for upcoming meetings and post intelligence briefings.
     * Called from SyncWorker every 2 minutes.
     */
    suspend fun checkAndBrief() {
        if (!prefs.getBoolean(KEY_ENABLED, true)) return

        val now = System.currentTimeMillis()
        val windowStart = now + 5 * 60_000L    // 5 minutes from now
        val windowEnd = now + 15 * 60_000L     // 15 minutes from now

        // Query calendar for events in the briefing window
        val events = getUpcomingEvents(windowStart, windowEnd)
        if (events.isEmpty()) return

        for (event in events) {
            if (event.id in briefedEventIds) continue
            briefedEventIds.add(event.id)

            // Prune old briefed IDs (keep last 20)
            if (briefedEventIds.size > 20) {
                val toRemove = briefedEventIds.take(briefedEventIds.size - 20)
                briefedEventIds.removeAll(toRemove.toSet())
            }

            val minutesUntil = ((event.startTime - now) / 60_000).toInt()
            Log.i(TAG, "Briefing for '${event.title}' in ${minutesUntil}min")

            try {
                val attendeeStr = event.attendees.joinToString(",")
                val response = apiClient.api().getMeetingPrep(
                    title = event.title,
                    attendees = attendeeStr,
                )

                if (!response.ok || response.briefing == null) {
                    postSimpleBriefing(event, minutesUntil)
                    continue
                }

                val briefing = response.briefing
                val bodyParts = mutableListOf<String>()

                bodyParts.add("Starts in ${minutesUntil}min")
                if (event.location.isNotBlank()) {
                    bodyParts.add("Location: ${event.location}")
                }
                if (event.attendees.isNotEmpty()) {
                    bodyParts.add("With: ${event.attendees.joinToString(", ")}")
                }

                // Add KG context
                for (fact in briefing.contextFacts.take(3)) {
                    bodyParts.add("${fact.about}: ${fact.fact}")
                }

                // Add memory context
                for (mem in briefing.recentMemories.take(2)) {
                    bodyParts.add("Previously: ${mem.summary}")
                }

                // Add suggested topics
                if (briefing.suggestedTopics.isNotEmpty()) {
                    bodyParts.add("Topics: ${briefing.suggestedTopics.take(3).joinToString(", ")}")
                }

                val body = bodyParts.joinToString("\n")
                postBriefingNotification(event.title, body, event.id.toInt())
            } catch (e: Exception) {
                Log.w(TAG, "Desktop unavailable for meeting prep: ${e.message}")
                postSimpleBriefing(event, minutesUntil)
            }
        }
    }

    private fun postSimpleBriefing(event: CalendarEvent, minutesUntil: Int) {
        val parts = mutableListOf("Starts in ${minutesUntil}min")
        if (event.location.isNotBlank()) parts.add("at ${event.location}")
        if (event.attendees.isNotEmpty()) parts.add("with ${event.attendees.joinToString(", ")}")
        postBriefingNotification(event.title, parts.joinToString(" "), event.id.toInt())
    }

    private fun postBriefingNotification(title: String, body: String, eventId: Int) {
        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.IMPORTANT.channelId,
        )
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Meeting Prep: $title")
            .setContentText(body.take(80))
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setGroup("jarvis_meeting_prep")
            .setTimeoutAfter(20 * 60_000L) // Dismiss after 20 minutes
            .build()

        notificationManager.notify(NOTIFICATION_ID_BASE + eventId, notification)
    }

    private fun getUpcomingEvents(windowStart: Long, windowEnd: Long): List<CalendarEvent> {
        val events = mutableListOf<CalendarEvent>()
        try {
            val instancesUri: Uri = CalendarContract.Instances.CONTENT_URI.let {
                ContentUris.appendId(
                    ContentUris.appendId(Uri.Builder().apply {
                        scheme(it.scheme)
                        authority(it.authority)
                        path(it.path)
                    }, windowStart),
                    windowEnd,
                ).build()
            }

            val projection = arrayOf(
                CalendarContract.Instances.EVENT_ID,
                CalendarContract.Instances.TITLE,
                CalendarContract.Instances.BEGIN,
                CalendarContract.Instances.EVENT_LOCATION,
            )

            val cursor: Cursor? = context.contentResolver.query(
                instancesUri, projection, null, null, "${CalendarContract.Instances.BEGIN} ASC",
            )

            cursor?.use {
                while (it.moveToNext()) {
                    val eventId = it.getLong(0)
                    val title = it.getString(1) ?: continue
                    val begin = it.getLong(2)
                    val location = it.getString(3) ?: ""

                    // Get attendees for this event
                    val attendees = getEventAttendees(eventId)

                    events.add(CalendarEvent(eventId, title, begin, location, attendees))
                }
            }
        } catch (e: SecurityException) {
            Log.w(TAG, "Calendar permission not granted: ${e.message}")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to query calendar: ${e.message}")
        }
        return events
    }

    private fun getEventAttendees(eventId: Long): List<String> {
        val attendees = mutableListOf<String>()
        try {
            val cursor = context.contentResolver.query(
                CalendarContract.Attendees.CONTENT_URI,
                arrayOf(CalendarContract.Attendees.ATTENDEE_NAME),
                "${CalendarContract.Attendees.EVENT_ID} = ?",
                arrayOf(eventId.toString()),
                null,
            )
            cursor?.use {
                while (it.moveToNext()) {
                    val name = it.getString(0)
                    if (!name.isNullOrBlank()) {
                        attendees.add(name)
                    }
                }
            }
        } catch (e: Exception) {
            Log.d(TAG, "Failed to get attendees for event $eventId: ${e.message}")
        }
        return attendees.take(10)
    }

    private data class CalendarEvent(
        val id: Long,
        val title: String,
        val startTime: Long,
        val location: String,
        val attendees: List<String>,
    )

    companion object {
        private const val TAG = "MeetingPrep"
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_ENABLED = "meeting_prep_enabled"
        private const val NOTIFICATION_ID_BASE = 8000
    }
}
