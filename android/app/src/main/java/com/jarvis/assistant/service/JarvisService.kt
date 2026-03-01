package com.jarvis.assistant.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.MainActivity
import com.jarvis.assistant.R
import com.jarvis.assistant.data.CommandQueueProcessor
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import com.jarvis.assistant.data.entity.ContextStateEntity
import com.jarvis.assistant.feature.callscreen.SpamDatabaseSync
import com.jarvis.assistant.feature.context.ContextAdjuster
import com.jarvis.assistant.feature.context.ContextDetector
import com.jarvis.assistant.feature.context.UserContext
import com.jarvis.assistant.feature.notifications.NotificationChannelManager
import com.jarvis.assistant.feature.notifications.ProactiveAlertReceiver
import com.jarvis.assistant.feature.commute.LocationLearner
import com.jarvis.assistant.feature.commute.ParkingMemory
import com.jarvis.assistant.feature.commute.TrafficChecker
import com.jarvis.assistant.feature.documents.DocumentSyncManager
import com.jarvis.assistant.feature.finance.SpendSummaryWorker
import com.jarvis.assistant.feature.habit.NudgeEngine
import com.jarvis.assistant.feature.habit.NudgeResponseTracker
import com.jarvis.assistant.feature.habit.PatternDetector
import com.jarvis.assistant.feature.prescription.MedicationScheduler
import com.jarvis.assistant.feature.prescription.RefillTracker
import com.jarvis.assistant.feature.social.RelationshipAlertEngine
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Foreground service that syncs the offline command queue with the
 * desktop engine at a configurable interval.
 */
@AndroidEntryPoint
class JarvisService : Service() {

    @Inject lateinit var processor: CommandQueueProcessor
    @Inject lateinit var spamDatabaseSync: SpamDatabaseSync
    @Inject lateinit var proactiveReceiver: ProactiveAlertReceiver
    @Inject lateinit var channelManager: NotificationChannelManager
    @Inject lateinit var contextDetector: ContextDetector
    @Inject lateinit var contextAdjuster: ContextAdjuster
    @Inject lateinit var contextStateDao: ContextStateDao
    @Inject lateinit var notificationLogDao: NotificationLogDao
    @Inject lateinit var nudgeLogDao: NudgeLogDao
    @Inject lateinit var callLogDao: CallLogDao
    @Inject lateinit var conversationDao: ConversationDao
    @Inject lateinit var medicationScheduler: MedicationScheduler
    @Inject lateinit var refillTracker: RefillTracker
    @Inject lateinit var locationLearner: LocationLearner
    @Inject lateinit var trafficChecker: TrafficChecker
    @Inject lateinit var parkingMemory: ParkingMemory
    @Inject lateinit var documentSyncManager: DocumentSyncManager
    @Inject lateinit var relationshipAlertEngine: RelationshipAlertEngine
    @Inject lateinit var patternDetector: PatternDetector
    @Inject lateinit var nudgeEngine: NudgeEngine
    @Inject lateinit var nudgeResponseTracker: NudgeResponseTracker

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var syncJob: Job? = null
    private var syncIntervalMs = DEFAULT_SYNC_MS
    // Initialize to current time so all periodic tasks wait one full interval
    // after service (re)start instead of all firing simultaneously.
    private var lastSpamSyncMs = System.currentTimeMillis()
    private var lastContextCheckMs = System.currentTimeMillis()
    private var lastRefillCheckMs = System.currentTimeMillis()
    private var lastLocationRecordMs = System.currentTimeMillis()
    private var lastTrafficCheckMs = System.currentTimeMillis()
    private var lastDocSyncMs = System.currentTimeMillis()
    private var lastRelationshipCheckMs = System.currentTimeMillis()
    private var lastPatternDetectionMs = System.currentTimeMillis()
    private var lastNudgeCheckMs = System.currentTimeMillis()
    private var lastNudgeExpiryMs = System.currentTimeMillis()
    private var lastCleanupMs = System.currentTimeMillis()
    @Volatile
    private var currentContext: UserContext = UserContext.NORMAL
    private val contextLock = Any()

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        channelManager.createChannels()
        startForeground(NOTIF_ID, buildNotification())
        try {
            parkingMemory.registerBluetoothReceiver()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to register Bluetooth receiver: ${e.message}")
        }
        try {
            SpendSummaryWorker.enqueue(applicationContext)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to enqueue spend summary worker: ${e.message}")
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        intent?.getLongExtra(EXTRA_SYNC_INTERVAL, -1)?.takeIf { it > 0 }?.let {
            syncIntervalMs = it
        }
        startSyncLoop()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        parkingMemory.unregisterBluetoothReceiver()
        scope.cancel()
        super.onDestroy()
    }

    private fun startSyncLoop() {
        syncJob?.cancel()
        syncJob = scope.launch {
            // Schedule all medication alarms on service start (ensures alarms
            // survive boot/restart).
            try {
                medicationScheduler.scheduleAllAlarms()
                Log.i(TAG, "Medication alarms scheduled on service start")
            } catch (e: Exception) {
                Log.w(TAG, "Failed to schedule medication alarms: ${e.message}")
            }

            while (isActive) {
                val now = System.currentTimeMillis()

                try {
                    processor.flushPending()
                } catch (e: Exception) {
                    Log.w(TAG, "Sync cycle error: ${e.message}")
                }

                // Spam DB sync: run at most every 10 minutes
                if (now - lastSpamSyncMs >= SPAM_SYNC_INTERVAL_MS) {
                    try {
                        spamDatabaseSync.syncFromDesktop()
                        lastSpamSyncMs = now
                    } catch (e: Exception) {
                        Log.w(TAG, "Spam DB sync error: ${e.message}")
                    }
                }

                // Proactive alert check: every sync cycle
                try {
                    proactiveReceiver.checkAndPost()
                } catch (e: Exception) {
                    Log.w(TAG, "Proactive alert check error: ${e.message}")
                }

                // Context detection: every 2 minutes
                if (now - lastContextCheckMs > CONTEXT_CHECK_INTERVAL_MS) {
                    lastContextCheckMs = now
                    try {
                        val state = contextDetector.detectCurrentContext()
                        synchronized(contextLock) {
                            if (state.context != currentContext) {
                                currentContext = state.context
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
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "Context detection error: ${e.message}")
                    }
                }

                // Refill check: every 6 hours
                if (now - lastRefillCheckMs >= REFILL_CHECK_INTERVAL_MS) {
                    lastRefillCheckMs = now
                    try {
                        refillTracker.checkRefills()
                    } catch (e: Exception) {
                        Log.w(TAG, "Refill check error: ${e.message}")
                    }
                }

                // Location recording: every 15 minutes
                if (now - lastLocationRecordMs >= LOCATION_RECORD_INTERVAL_MS) {
                    lastLocationRecordMs = now
                    try {
                        locationLearner.recordLocation()
                    } catch (e: Exception) {
                        Log.w(TAG, "Location recording error: ${e.message}")
                    }
                }

                // Traffic check: every 30 minutes
                if (now - lastTrafficCheckMs >= TRAFFIC_CHECK_INTERVAL_MS) {
                    lastTrafficCheckMs = now
                    try {
                        trafficChecker.checkPreDeparture()
                    } catch (e: Exception) {
                        Log.w(TAG, "Traffic check error: ${e.message}")
                    }
                }

                // Document sync: every 5 minutes (if auto-sync enabled)
                if (now - lastDocSyncMs >= DOC_SYNC_INTERVAL_MS) {
                    lastDocSyncMs = now
                    val docAutoSync = getSharedPreferences(
                        "jarvis_prefs", MODE_PRIVATE,
                    ).getBoolean("doc_auto_sync", true)
                    if (docAutoSync) {
                        try {
                            documentSyncManager.syncPending()
                        } catch (e: Exception) {
                            Log.w(TAG, "Document sync error: ${e.message}")
                        }
                    }
                }

                // Relationship alerts: once per day
                if (now - lastRelationshipCheckMs >= RELATIONSHIP_CHECK_INTERVAL_MS) {
                    lastRelationshipCheckMs = now
                    try {
                        relationshipAlertEngine.checkRelationshipAlerts()
                    } catch (e: Exception) {
                        Log.w(TAG, "Relationship alert check error: ${e.message}")
                    }
                }

                // Pattern detection: once per day (habit engine)
                if (now - lastPatternDetectionMs >= PATTERN_DETECTION_INTERVAL_MS) {
                    lastPatternDetectionMs = now
                    try {
                        patternDetector.detectPatterns()
                    } catch (e: Exception) {
                        Log.w(TAG, "Pattern detection error: ${e.message}")
                    }
                }

                // Nudge check: every 5 minutes (habit engine)
                if (now - lastNudgeCheckMs >= NUDGE_CHECK_INTERVAL_MS) {
                    lastNudgeCheckMs = now
                    val nudgesEnabled = getSharedPreferences(
                        "jarvis_prefs", MODE_PRIVATE,
                    ).getBoolean("habit_nudges_enabled", true)
                    if (nudgesEnabled) {
                        try {
                            nudgeEngine.checkAndDeliver()
                        } catch (e: Exception) {
                            Log.w(TAG, "Nudge check error: ${e.message}")
                        }
                    }
                }

                // Expire stale nudges: every hour (habit engine)
                if (now - lastNudgeExpiryMs >= NUDGE_EXPIRY_INTERVAL_MS) {
                    lastNudgeExpiryMs = now
                    try {
                        nudgeResponseTracker.expireStaleNudges()
                    } catch (e: Exception) {
                        Log.w(TAG, "Nudge expiry error: ${e.message}")
                    }
                }

                // Database cleanup: every 24 hours, delete records older than 90 days
                if (now - lastCleanupMs >= CLEANUP_INTERVAL_MS) {
                    lastCleanupMs = now
                    try {
                        val cutoff = now - RETENTION_PERIOD_MS
                        notificationLogDao.deleteOlderThan(cutoff)
                        nudgeLogDao.deleteOlderThan(cutoff)
                        callLogDao.deleteOlderThan(cutoff)
                        conversationDao.deleteOlderThan(cutoff)
                        contextStateDao.deleteOld(cutoff)
                        Log.i(TAG, "Database cleanup completed (removed records older than 90 days)")
                    } catch (e: Exception) {
                        Log.w(TAG, "Database cleanup error: ${e.message}")
                    }
                }

                delay(syncIntervalMs)
            }
        }
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Jarvis Sync",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Keeps Jarvis connected to the desktop engine"
        }
        getSystemService(NotificationManager::class.java)
            .createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val openIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )

        val voiceIntent = PendingIntent.getActivity(
            this, 1,
            Intent(this, MainActivity::class.java).apply {
                putExtra(EXTRA_VOICE_COMMAND, true)
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Jarvis is running")
            .setContentText("Syncing with desktop engine")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .addAction(
                android.R.drawable.ic_btn_speak_now,
                "Talk to Jarvis",
                voiceIntent,
            )
            .build()
    }

    companion object {
        const val CHANNEL_ID = "jarvis_sync"
        const val NOTIF_ID = 1
        const val EXTRA_SYNC_INTERVAL = "sync_interval_ms"
        const val EXTRA_VOICE_COMMAND = "voice_command"
        private const val TAG = "JarvisService"
        private const val DEFAULT_SYNC_MS = 30_000L
        private const val SPAM_SYNC_INTERVAL_MS = 10L * 60 * 1000 // 10 minutes
        private const val CONTEXT_CHECK_INTERVAL_MS = 120_000L // 2 minutes
        private const val REFILL_CHECK_INTERVAL_MS = 6L * 60 * 60 * 1000 // 6 hours
        private const val LOCATION_RECORD_INTERVAL_MS = 15L * 60 * 1000 // 15 minutes
        private const val TRAFFIC_CHECK_INTERVAL_MS = 30L * 60 * 1000 // 30 minutes
        private const val DOC_SYNC_INTERVAL_MS = 5L * 60 * 1000 // 5 minutes
        private const val RELATIONSHIP_CHECK_INTERVAL_MS = 24L * 60 * 60 * 1000 // 24 hours
        private const val PATTERN_DETECTION_INTERVAL_MS = 24L * 60 * 60 * 1000 // 24 hours
        private const val NUDGE_CHECK_INTERVAL_MS = 5L * 60 * 1000 // 5 minutes
        private const val NUDGE_EXPIRY_INTERVAL_MS = 1L * 60 * 60 * 1000 // 1 hour
        private const val CLEANUP_INTERVAL_MS = 24L * 60 * 60 * 1000 // 24 hours
        private const val RETENTION_PERIOD_MS = 90L * 24 * 60 * 60 * 1000 // 90 days
    }
}
