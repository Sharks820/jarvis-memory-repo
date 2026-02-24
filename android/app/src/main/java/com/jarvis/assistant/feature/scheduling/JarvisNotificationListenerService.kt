package com.jarvis.assistant.feature.scheduling

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * System-wide notification listener that intercepts SMS and email notifications,
 * extracts scheduling cues (dates, times, locations), and creates calendar events
 * automatically when confidence exceeds the user-configured threshold.
 *
 * Requires the user to grant Notification Access in Android Settings.
 *
 * NOTE: NotificationListenerService cannot use @AndroidEntryPoint directly because
 * it is not a standard Hilt-supported lifecycle component. We use @EntryPoint with
 * EntryPointAccessors for manual Hilt injection instead.
 */
class JarvisNotificationListenerService : NotificationListenerService() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface SchedulingEntryPoint {
        fun cueExtractor(): SchedulingCueExtractor
        fun calendarCreator(): CalendarEventCreator
    }

    private val cueExtractor by lazy {
        EntryPointAccessors.fromApplication(
            application,
            SchedulingEntryPoint::class.java,
        ).cueExtractor()
    }

    private val calendarCreator by lazy {
        EntryPointAccessors.fromApplication(
            application,
            SchedulingEntryPoint::class.java,
        ).calendarCreator()
    }

    /**
     * Package filter: only process notifications from SMS and email apps.
     * These are the standard messaging and email apps on Samsung and stock Android.
     */
    private val SCHEDULING_PACKAGES = setOf(
        "com.google.android.apps.messaging",   // Google Messages
        "com.samsung.android.messaging",        // Samsung Messages
        "com.google.android.gm",                // Gmail
        "com.microsoft.office.outlook",          // Outlook
        "com.samsung.android.email.provider",    // Samsung Email
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val pkg = sbn.packageName
        if (pkg !in SCHEDULING_PACKAGES) return

        // Check if scheduling extraction is enabled in user preferences
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_EXTRACTION_ENABLED, true)) return

        val extras = sbn.notification.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""

        // Prefer bigText (more content) with title, fall back to text
        val fullText = "$title\n${bigText.ifBlank { text }}"
        if (fullText.isBlank() || fullText.length < 10) return

        scope.launch {
            try {
                val cues = cueExtractor.extract(fullText, pkg)
                for (cue in cues) {
                    if (cue.confidence < MIN_CONFIDENCE) continue

                    // Auto-create calendar event if confidence meets threshold
                    val autoCreateThreshold = prefs.getFloat(
                        KEY_AUTO_CREATE_THRESHOLD,
                        DEFAULT_AUTO_CREATE_THRESHOLD,
                    )
                    if (cue.confidence >= autoCreateThreshold) {
                        val eventId = calendarCreator.createEvent(cue)
                        if (eventId > 0) {
                            calendarCreator.notifyDesktopOfEvent(cue)
                        }
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "Cue extraction failed: ${e.message}")
            }
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        // No action needed on removal
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JarvisScheduling"

        /** SharedPreferences file for scheduling settings. */
        const val PREFS_NAME = "jarvis_prefs"

        /** Key: enable/disable scheduling extraction from notifications. */
        const val KEY_EXTRACTION_ENABLED = "scheduling_extraction_enabled"

        /** Key: confidence threshold for auto-creating calendar events. */
        const val KEY_AUTO_CREATE_THRESHOLD = "scheduling_auto_create_threshold"

        /** Default auto-create threshold (0.5 = date + time required). */
        const val DEFAULT_AUTO_CREATE_THRESHOLD = 0.5f

        /** Minimum confidence to even consider a cue (date-only = 0.3). */
        private const val MIN_CONFIDENCE = 0.3f
    }
}
