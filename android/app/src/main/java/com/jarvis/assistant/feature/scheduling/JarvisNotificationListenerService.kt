package com.jarvis.assistant.feature.scheduling

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import com.jarvis.assistant.feature.finance.BankNotificationParser
import com.jarvis.assistant.feature.notifications.NotificationLearner
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
        fun notificationLearner(): NotificationLearner
        fun bankNotificationParser(): BankNotificationParser
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

    private val notificationLearner by lazy {
        EntryPointAccessors.fromApplication(
            application,
            SchedulingEntryPoint::class.java,
        ).notificationLearner()
    }

    private val bankNotificationParser by lazy {
        EntryPointAccessors.fromApplication(
            application,
            SchedulingEntryPoint::class.java,
        ).bankNotificationParser()
    }

    /** Track notification post times for calculating action delay. */
    private val notificationPostTimes = java.util.concurrent.ConcurrentHashMap<Int, Long>()

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
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        // Track Jarvis notification post times for learning
        val channelId = sbn.notification.channelId ?: ""
        if (channelId.startsWith("jarvis_") && channelId != "jarvis_sync") {
            notificationPostTimes[sbn.id] = System.currentTimeMillis()
        }

        val extras = sbn.notification.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
        val bigText = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""

        // Prefer bigText (more content) with title, fall back to text
        val fullText = "$title\n${bigText.ifBlank { text }}"
        if (fullText.isBlank() || fullText.length < 10) return

        // ── Financial notification parsing ─────────────────────
        if (prefs.getBoolean(KEY_FINANCE_MONITORING_ENABLED, true) &&
            bankNotificationParser.isBankApp(pkg)
        ) {
            scope.launch {
                try {
                    bankNotificationParser.parseAndStore(pkg, fullText)
                } catch (e: Exception) {
                    Log.w(TAG, "Bank notification parsing failed: ${e.message}")
                }
            }
        }

        // ── Scheduling cue extraction ──────────────────────────
        if (pkg !in SCHEDULING_PACKAGES) return
        if (!prefs.getBoolean(KEY_EXTRACTION_ENABLED, true)) return

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
        // Clean up tracking
        notificationPostTimes.remove(sbn.id)
    }

    override fun onNotificationRemoved(
        sbn: StatusBarNotification,
        rankingMap: RankingMap,
        reason: Int,
    ) {
        val channelId = sbn.notification.channelId ?: ""
        // Only log interactions with Jarvis notification channels (not the sync channel)
        if (!channelId.startsWith("jarvis_") || channelId == "jarvis_sync") {
            notificationPostTimes.remove(sbn.id)
            return
        }

        val action = when (reason) {
            REASON_CLICK -> "acted"
            REASON_CANCEL -> "dismissed"
            REASON_APP_CANCEL -> "expired"  // System/app removal, not user engagement
            else -> "expired"
        }

        val postTime = notificationPostTimes.remove(sbn.id) ?: 0L
        val delayMs = if (postTime > 0) System.currentTimeMillis() - postTime else 0L

        val extras = sbn.notification.extras
        val title = extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val alertType = extras?.getString(EXTRA_ALERT_TYPE) ?: channelId

        scope.launch {
            try {
                notificationLearner.logAction(
                    notificationId = sbn.id,
                    alertType = alertType,
                    title = title,
                    channelId = channelId,
                    action = action,
                    actionDelayMs = delayMs,
                )
            } catch (e: Exception) {
                Log.w(TAG, "Failed to log notification action: ${e.message}")
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JarvisScheduling"

        /** Notification extra key used to tag the desktop alert type. */
        private const val EXTRA_ALERT_TYPE = "jarvis_alert_type"

        // Removal reason constants (mirrors NotificationListenerService constants)
        private const val REASON_CLICK = 1
        private const val REASON_CANCEL = 2
        private const val REASON_APP_CANCEL = 8

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

        /** Key: enable/disable financial monitoring from notifications. */
        const val KEY_FINANCE_MONITORING_ENABLED = "finance_monitoring_enabled"
    }
}
