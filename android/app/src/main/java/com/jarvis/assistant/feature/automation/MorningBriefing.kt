package com.jarvis.assistant.feature.automation

import android.app.NotificationManager
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.feature.notifications.NotificationPriority
import com.jarvis.assistant.feature.voice.VoiceEngine
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Calendar
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Morning intelligence briefing.
 *
 * When the user wakes up (context transitions from SLEEPING → NORMAL),
 * Jarvis automatically:
 * 1. Runs "ops-brief" via desktop brain for today's briefing
 * 2. Posts a rich notification with the briefing content
 * 3. Optionally speaks the briefing aloud via TTS
 *
 * The briefing includes:
 * - Today's calendar events
 * - Pending tasks and their priorities
 * - Overnight proactive alerts
 * - Weather/commute info (if available in KG)
 *
 * This replaces the manual step of opening the app and checking
 * everything individually after waking up.
 */
@Singleton
class MorningBriefing @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiClient: JarvisApiClient,
    private val voiceEngine: VoiceEngine,
) {

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /** Track last briefing day to avoid duplicates (persisted across process death). */
    private var lastBriefingDay: Int
        get() = prefs.getInt("last_briefing_day", -1)
        set(value) = prefs.edit().putInt("last_briefing_day", value).apply()

    /**
     * Called when user exits SLEEPING context.
     * Generates and delivers the morning briefing.
     */
    suspend fun onWakeUp() {
        if (!prefs.getBoolean(KEY_ENABLED, true)) return

        // Only deliver once per calendar day
        val today = Calendar.getInstance().get(Calendar.DAY_OF_YEAR)
        if (today == lastBriefingDay) return
        lastBriefingDay = today

        Log.i(TAG, "Generating morning briefing")

        try {
            // Ask desktop for the morning ops brief
            val response = apiClient.api().sendCommand(
                CommandRequest(
                    text = "Jarvis, give me my morning briefing",
                    execute = false,
                    speak = false,
                ),
            )

            if (!response.ok) {
                Log.w(TAG, "Desktop unavailable for morning briefing")
                return
            }

            // Extract the briefing from the response
            val briefingText = buildString {
                if (response.reason.isNotBlank()) {
                    append(response.reason)
                }
                for (line in response.stdoutTail) {
                    if (line.isNotBlank() && !line.startsWith("intent=")) {
                        if (isNotEmpty()) append("\n")
                        append(line)
                    }
                }
            }.trim()

            if (briefingText.isBlank()) return

            // Post notification
            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.ROUTINE.channelId,
            )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Good Morning — Jarvis Briefing")
                .setContentText(briefingText.take(80))
                .setStyle(NotificationCompat.BigTextStyle().bigText(briefingText.take(500)))
                .setAutoCancel(true)
                .setGroup("jarvis_morning")
                .setTimeoutAfter(4 * 60 * 60 * 1000L) // Dismiss after 4 hours
                .build()

            notificationManager.notify(NOTIFICATION_ID, notification)

            // Optionally speak the briefing
            if (prefs.getBoolean(KEY_SPEAK, false)) {
                voiceEngine.speak(briefingText.take(300))
            }

            Log.i(TAG, "Morning briefing delivered (${briefingText.length} chars)")
        } catch (e: Exception) {
            Log.w(TAG, "Morning briefing failed: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "MorningBrief"
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_ENABLED = "morning_briefing_enabled"
        const val KEY_SPEAK = "morning_briefing_speak"
        private const val NOTIFICATION_ID = 9002
    }
}
