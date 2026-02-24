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
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.entity.ContextStateEntity
import com.jarvis.assistant.feature.callscreen.SpamDatabaseSync
import com.jarvis.assistant.feature.context.ContextAdjuster
import com.jarvis.assistant.feature.context.ContextDetector
import com.jarvis.assistant.feature.context.UserContext
import com.jarvis.assistant.feature.notifications.NotificationChannelManager
import com.jarvis.assistant.feature.notifications.ProactiveAlertReceiver
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

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var syncJob: Job? = null
    private var syncIntervalMs = DEFAULT_SYNC_MS
    private var lastSpamSyncMs = 0L
    private var lastContextCheckMs = 0L
    private var currentContext: UserContext = UserContext.NORMAL

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        channelManager.createChannels()
        startForeground(NOTIF_ID, buildNotification())
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
        scope.cancel()
        super.onDestroy()
    }

    private fun startSyncLoop() {
        syncJob?.cancel()
        syncJob = scope.launch {
            while (isActive) {
                try {
                    processor.flushPending()
                } catch (e: Exception) {
                    Log.w(TAG, "Sync cycle error: ${e.message}")
                }

                // Spam DB sync: run at most every 10 minutes
                val now = System.currentTimeMillis()
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
                val contextNow = System.currentTimeMillis()
                if (contextNow - lastContextCheckMs > CONTEXT_CHECK_INTERVAL_MS) {
                    lastContextCheckMs = contextNow
                    try {
                        val state = contextDetector.detectCurrentContext()
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
                    } catch (e: Exception) {
                        Log.w(TAG, "Context detection error: ${e.message}")
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
    }
}
