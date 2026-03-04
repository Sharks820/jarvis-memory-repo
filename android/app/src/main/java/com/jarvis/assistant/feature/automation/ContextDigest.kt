package com.jarvis.assistant.feature.automation

import android.app.NotificationManager
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.feature.context.UserContext
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Generates a digest of what the user missed when exiting a busy context.
 *
 * When ContextAdjuster transitions from MEETING/DRIVING/SLEEPING → NORMAL,
 * it calls [onContextExit] which:
 * 1. Queries desktop GET /digest?since=<entry_ts>&context=<label>
 * 2. Builds a human-readable summary of missed items
 * 3. Posts a ROUTINE notification with the digest
 *
 * This removes the manual step of checking notifications, calendar, and
 * missed calls after leaving a meeting or arriving from a drive.
 */
@Singleton
class ContextDigest @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiClient: JarvisApiClient,
) {

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /** Timestamp (millis) when the user entered the busy context. */
    @Volatile
    private var contextEntryTime: Long = 0

    /** Label of the context being entered. */
    @Volatile
    private var activeContext: String = ""

    /**
     * Called when user ENTERS a busy context (meeting, driving, sleeping).
     * Records the timestamp so we know what to include in the digest.
     */
    fun onContextEnter(userContext: UserContext) {
        if (userContext == UserContext.NORMAL || userContext == UserContext.GAMING) return
        contextEntryTime = System.currentTimeMillis()
        activeContext = userContext.label.lowercase()
        Log.d(TAG, "Context entered: $activeContext at $contextEntryTime")
    }

    /**
     * Called when user EXITS a busy context (transition to NORMAL).
     * Fetches and posts the digest.
     */
    suspend fun onContextExit(previousContext: UserContext) {
        if (!prefs.getBoolean(KEY_ENABLED, true)) return
        if (previousContext == UserContext.NORMAL || previousContext == UserContext.GAMING) return
        if (contextEntryTime == 0L) return

        val sinceTs = contextEntryTime / 1000  // Convert to unix seconds
        val contextLabel = previousContext.label.lowercase()
        val entryTime = contextEntryTime
        contextEntryTime = 0
        activeContext = ""

        // Only generate digest if user was busy for at least 5 minutes
        val durationMinutes = (System.currentTimeMillis() - entryTime) / 60_000
        if (durationMinutes < 5) {
            Log.d(TAG, "Context too short ($durationMinutes min), skipping digest")
            return
        }

        Log.i(TAG, "Generating digest after $durationMinutes min in $contextLabel")

        try {
            val response = apiClient.api().getDigest(since = sinceTs, context = contextLabel)
            if (!response.ok || response.digest == null) return

            val digest = response.digest
            val parts = mutableListOf<String>()

            // Summarize what was missed
            val alertCount = digest.proactiveAlerts.size
            if (alertCount > 0) {
                parts.add("$alertCount alert${if (alertCount > 1) "s" else ""}")
            }

            val upcomingCount = digest.calendarUpcoming.size
            if (upcomingCount > 0) {
                val nextEvent = digest.calendarUpcoming.firstOrNull()
                val nextTitle = nextEvent?.get("title")?.toString() ?: "event"
                val nextMin = nextEvent?.get("minutes_until")?.toString() ?: "?"
                parts.add("Next: $nextTitle in ${nextMin}min")
            }

            if (digest.notificationsSummary.isNotBlank()) {
                parts.add(digest.notificationsSummary.take(100))
            }

            if (parts.isEmpty()) {
                Log.d(TAG, "Nothing to report in digest")
                return
            }

            val contextEmoji = when (contextLabel) {
                "meeting" -> "You were in a meeting for ${durationMinutes}min"
                "driving" -> "You were driving for ${durationMinutes}min"
                "sleeping" -> "Good morning! You slept for ${durationMinutes / 60}h ${durationMinutes % 60}m"
                else -> "While you were away for ${durationMinutes}min"
            }

            val body = "$contextEmoji. ${parts.joinToString(" | ")}"

            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.ROUTINE.channelId,
            )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Jarvis Digest")
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .setGroup("jarvis_digest")
                .build()

            notificationManager.notify(NOTIFICATION_ID, notification)
            Log.i(TAG, "Posted digest: $body")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to generate digest: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "ContextDigest"
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_ENABLED = "context_digest_enabled"
        private const val NOTIFICATION_ID = 9001
    }
}
