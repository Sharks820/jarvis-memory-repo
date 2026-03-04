package com.jarvis.assistant.service

import android.content.Context
import android.util.Log
import androidx.hilt.work.HiltWorker
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import com.jarvis.assistant.data.entity.ContextStateEntity
import com.jarvis.assistant.feature.callscreen.SpamDatabaseSync
import com.jarvis.assistant.feature.commute.LocationLearner
import com.jarvis.assistant.feature.commute.TrafficChecker
import com.jarvis.assistant.feature.context.ContextAdjuster
import com.jarvis.assistant.feature.context.ContextDetector
import com.jarvis.assistant.feature.documents.DocumentSyncManager
import com.jarvis.assistant.feature.habit.NudgeEngine
import com.jarvis.assistant.feature.habit.NudgeResponseTracker
import com.jarvis.assistant.feature.habit.PatternDetector
import com.jarvis.assistant.feature.prescription.RefillTracker
import com.jarvis.assistant.feature.automation.MeetingPrepService
import com.jarvis.assistant.feature.automation.RelationshipAutopilot
import com.jarvis.assistant.feature.social.RelationshipAlertEngine
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject

/**
 * WorkManager-based periodic sync worker that handles non-time-critical
 * periodic tasks alongside [JarvisService].
 *
 * Command queue flush and proactive alert checks are handled by
 * JarvisService's high-frequency coroutine loop (30s interval).
 *
 * This worker handles:
 * - Spam DB sync (throttled to 10-minute intervals)
 * - Context detection (every 2 minutes)
 * - Refill checks (every 6 hours)
 * - Location recording (every 15 minutes)
 * - Traffic checks (every 30 minutes)
 * - Document sync (every 5 minutes, if enabled)
 * - Relationship alerts (daily)
 * - Pattern detection (daily)
 * - Nudge checks (every 5 minutes, if enabled)
 * - Nudge expiry (hourly)
 * - Database cleanup (daily, 90-day retention)
 *
 * Uses [HiltWorker] with [AssistedInject] for Hilt dependency injection,
 * matching the pattern established by [com.jarvis.assistant.feature.finance.SpendSummaryWorker].
 *
 * Periodic task intervals that exceed the 2-minute WorkManager period are
 * tracked via SharedPreferences timestamps so they survive worker restarts.
 */
@HiltWorker
class SyncWorker @AssistedInject constructor(
    @Assisted appContext: Context,
    @Assisted params: WorkerParameters,
    private val spamDatabaseSync: SpamDatabaseSync,
    private val contextDetector: ContextDetector,
    private val contextAdjuster: ContextAdjuster,
    private val contextStateDao: ContextStateDao,
    private val notificationLogDao: NotificationLogDao,
    private val nudgeLogDao: NudgeLogDao,
    private val callLogDao: CallLogDao,
    private val conversationDao: ConversationDao,
    private val refillTracker: RefillTracker,
    private val locationLearner: LocationLearner,
    private val trafficChecker: TrafficChecker,
    private val documentSyncManager: DocumentSyncManager,
    private val relationshipAlertEngine: RelationshipAlertEngine,
    private val patternDetector: PatternDetector,
    private val nudgeEngine: NudgeEngine,
    private val nudgeResponseTracker: NudgeResponseTracker,
    private val meetingPrepService: MeetingPrepService,
    private val relationshipAutopilot: RelationshipAutopilot,
) : CoroutineWorker(appContext, params) {

    private val prefs by lazy {
        applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    override suspend fun doWork(): Result {
        val now = System.currentTimeMillis()

        // NOTE: Command queue flush and proactive alert checks are handled by
        // JarvisService's high-frequency coroutine loop (30s interval) and are
        // intentionally NOT duplicated here. SyncWorker handles only tasks that
        // are fine with WorkManager's 15-minute minimum period.

        // 1. Spam DB sync: at most every 10 minutes
        if (now - getLastTimestamp(KEY_LAST_SPAM_SYNC) >= SPAM_SYNC_INTERVAL_MS) {
            try {
                spamDatabaseSync.syncFromDesktop()
                saveTimestamp(KEY_LAST_SPAM_SYNC, now)
            } catch (e: Exception) {
                Log.w(TAG, "Spam DB sync error: ${e.message}")
            }
        }

        // 2. Context detection: every 2 minutes
        if (now - getLastTimestamp(KEY_LAST_CONTEXT_CHECK) >= CONTEXT_CHECK_INTERVAL_MS) {
            try {
                val state = contextDetector.detectCurrentContext()
                val lastContextName = prefs.getString(KEY_CURRENT_CONTEXT, null)
                if (state.context.name != lastContextName) {
                    prefs.edit().putString(KEY_CURRENT_CONTEXT, state.context.name).apply()
                    contextAdjuster.applyContext(state)
                    contextStateDao.insert(
                        ContextStateEntity(
                            context = state.context.name,
                            confidence = state.confidence,
                            source = state.source,
                        ),
                    )
                    Log.i(TAG, "Context changed to: ${state.context.label}")
                }
                saveTimestamp(KEY_LAST_CONTEXT_CHECK, now)
            } catch (e: Exception) {
                Log.w(TAG, "Context detection error: ${e.message}")
            }
        }

        // 5. Refill check: every 6 hours
        if (now - getLastTimestamp(KEY_LAST_REFILL_CHECK) >= REFILL_CHECK_INTERVAL_MS) {
            try {
                refillTracker.checkRefills()
                saveTimestamp(KEY_LAST_REFILL_CHECK, now)
            } catch (e: Exception) {
                Log.w(TAG, "Refill check error: ${e.message}")
            }
        }

        // 6. Location recording: every 15 minutes
        if (now - getLastTimestamp(KEY_LAST_LOCATION_RECORD) >= LOCATION_RECORD_INTERVAL_MS) {
            try {
                locationLearner.recordLocation()
                saveTimestamp(KEY_LAST_LOCATION_RECORD, now)
            } catch (e: Exception) {
                Log.w(TAG, "Location recording error: ${e.message}")
            }
        }

        // 7. Traffic check: every 30 minutes (if enabled)
        if (now - getLastTimestamp(KEY_LAST_TRAFFIC_CHECK) >= TRAFFIC_CHECK_INTERVAL_MS) {
            val jarvisPrefs = applicationContext.getSharedPreferences(
                "jarvis_prefs", Context.MODE_PRIVATE,
            )
            val trafficEnabled = jarvisPrefs.getBoolean("traffic_alerts", true)
            if (trafficEnabled) {
                try {
                    trafficChecker.checkPreDeparture()
                    saveTimestamp(KEY_LAST_TRAFFIC_CHECK, now)
                } catch (e: Exception) {
                    Log.w(TAG, "Traffic check error: ${e.message}")
                }
            }
        }

        // 8. Document sync: every 5 minutes (if auto-sync enabled)
        if (now - getLastTimestamp(KEY_LAST_DOC_SYNC) >= DOC_SYNC_INTERVAL_MS) {
            val jarvisPrefs = applicationContext.getSharedPreferences(
                "jarvis_prefs", Context.MODE_PRIVATE,
            )
            val docAutoSync = jarvisPrefs.getBoolean("doc_auto_sync", true)
            if (docAutoSync) {
                try {
                    documentSyncManager.syncPending()
                    saveTimestamp(KEY_LAST_DOC_SYNC, now)
                } catch (e: Exception) {
                    Log.w(TAG, "Document sync error: ${e.message}")
                }
            }
        }

        // 9. Relationship alerts: once per day
        if (now - getLastTimestamp(KEY_LAST_RELATIONSHIP_CHECK) >= RELATIONSHIP_CHECK_INTERVAL_MS) {
            try {
                relationshipAlertEngine.checkRelationshipAlerts()
                relationshipAutopilot.checkNeglectedContacts()
                saveTimestamp(KEY_LAST_RELATIONSHIP_CHECK, now)
            } catch (e: Exception) {
                Log.w(TAG, "Relationship alert check error: ${e.message}")
            }
        }

        // 9b. Meeting prep: every 2 minutes (checks for events starting in 5-15min)
        if (now - getLastTimestamp(KEY_LAST_MEETING_PREP) >= MEETING_PREP_INTERVAL_MS) {
            try {
                meetingPrepService.checkAndBrief()
                saveTimestamp(KEY_LAST_MEETING_PREP, now)
            } catch (e: Exception) {
                Log.w(TAG, "Meeting prep error: ${e.message}")
            }
        }

        // 10. Pattern detection: once per day
        if (now - getLastTimestamp(KEY_LAST_PATTERN_DETECTION) >= PATTERN_DETECTION_INTERVAL_MS) {
            try {
                patternDetector.detectPatterns()
                saveTimestamp(KEY_LAST_PATTERN_DETECTION, now)
            } catch (e: Exception) {
                Log.w(TAG, "Pattern detection error: ${e.message}")
            }
        }

        // 11. Nudge check: every 5 minutes
        if (now - getLastTimestamp(KEY_LAST_NUDGE_CHECK) >= NUDGE_CHECK_INTERVAL_MS) {
            val jarvisPrefs = applicationContext.getSharedPreferences(
                "jarvis_prefs", Context.MODE_PRIVATE,
            )
            val nudgesEnabled = jarvisPrefs.getBoolean("habit_nudges_enabled", true)
            if (nudgesEnabled) {
                try {
                    nudgeEngine.checkAndDeliver()
                    saveTimestamp(KEY_LAST_NUDGE_CHECK, now)
                } catch (e: Exception) {
                    Log.w(TAG, "Nudge check error: ${e.message}")
                }
            }
        }

        // 12. Expire stale nudges: every hour
        if (now - getLastTimestamp(KEY_LAST_NUDGE_EXPIRY) >= NUDGE_EXPIRY_INTERVAL_MS) {
            try {
                nudgeResponseTracker.expireStaleNudges()
                saveTimestamp(KEY_LAST_NUDGE_EXPIRY, now)
            } catch (e: Exception) {
                Log.w(TAG, "Nudge expiry error: ${e.message}")
            }
        }

        // 13. Database cleanup: every 24 hours (90-day retention)
        if (now - getLastTimestamp(KEY_LAST_CLEANUP) >= CLEANUP_INTERVAL_MS) {
            try {
                val cutoff = now - RETENTION_PERIOD_MS
                notificationLogDao.deleteOlderThan(cutoff)
                nudgeLogDao.deleteOlderThan(cutoff)
                callLogDao.deleteOlderThan(cutoff)
                conversationDao.deleteOlderThan(cutoff)
                contextStateDao.deleteOld(cutoff)
                saveTimestamp(KEY_LAST_CLEANUP, now)
                Log.i(TAG, "Database cleanup completed (removed records older than 90 days)")
            } catch (e: Exception) {
                Log.w(TAG, "Database cleanup error: ${e.message}")
            }
        }

        return Result.success()
    }

    private fun getLastTimestamp(key: String): Long = prefs.getLong(key, 0L)

    private fun saveTimestamp(key: String, value: Long) {
        prefs.edit().putLong(key, value).apply()
    }

    companion object {
        private const val TAG = "SyncWorker"
        const val WORK_NAME = "jarvis_sync"
        const val PREFS_NAME = "jarvis_sync_worker_timestamps"

        // SharedPreferences keys for throttled task timestamps
        private const val KEY_LAST_SPAM_SYNC = "last_spam_sync"
        private const val KEY_LAST_CONTEXT_CHECK = "last_context_check"
        private const val KEY_LAST_REFILL_CHECK = "last_refill_check"
        private const val KEY_LAST_LOCATION_RECORD = "last_location_record"
        private const val KEY_LAST_TRAFFIC_CHECK = "last_traffic_check"
        private const val KEY_LAST_DOC_SYNC = "last_doc_sync"
        private const val KEY_LAST_RELATIONSHIP_CHECK = "last_relationship_check"
        private const val KEY_LAST_PATTERN_DETECTION = "last_pattern_detection"
        private const val KEY_LAST_NUDGE_CHECK = "last_nudge_check"
        private const val KEY_LAST_NUDGE_EXPIRY = "last_nudge_expiry"
        private const val KEY_LAST_CLEANUP = "last_cleanup"
        private const val KEY_LAST_MEETING_PREP = "last_meeting_prep"
        private const val KEY_CURRENT_CONTEXT = "current_context"

        // Interval constants (same as JarvisService)
        private const val SPAM_SYNC_INTERVAL_MS = 10L * 60 * 1000 // 10 minutes
        private const val CONTEXT_CHECK_INTERVAL_MS = 120_000L // 2 minutes
        private const val REFILL_CHECK_INTERVAL_MS = 6L * 60 * 60 * 1000 // 6 hours
        private const val LOCATION_RECORD_INTERVAL_MS = 15L * 60 * 1000 // 15 minutes
        private const val TRAFFIC_CHECK_INTERVAL_MS = 30L * 60 * 1000 // 30 minutes
        private const val DOC_SYNC_INTERVAL_MS = 5L * 60 * 1000 // 5 minutes
        private const val MEETING_PREP_INTERVAL_MS = 120_000L // 2 minutes
        private const val RELATIONSHIP_CHECK_INTERVAL_MS = 24L * 60 * 60 * 1000 // 24 hours
        private const val PATTERN_DETECTION_INTERVAL_MS = 24L * 60 * 60 * 1000 // 24 hours
        private const val NUDGE_CHECK_INTERVAL_MS = 5L * 60 * 1000 // 5 minutes
        private const val NUDGE_EXPIRY_INTERVAL_MS = 1L * 60 * 60 * 1000 // 1 hour
        private const val CLEANUP_INTERVAL_MS = 24L * 60 * 60 * 1000 // 24 hours
        private const val RETENTION_PERIOD_MS = 90L * 24 * 60 * 60 * 1000 // 90 days
    }
}
