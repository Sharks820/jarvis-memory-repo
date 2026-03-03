package com.jarvis.assistant.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.jarvis.assistant.MainActivity
import com.jarvis.assistant.R
import com.jarvis.assistant.feature.commute.ParkingMemory
import com.jarvis.assistant.feature.finance.SpendSummaryWorker
import com.jarvis.assistant.feature.notifications.NotificationChannelManager
import com.jarvis.assistant.data.CommandQueueProcessor
import com.jarvis.assistant.feature.notifications.ProactiveAlertReceiver
import com.jarvis.assistant.feature.prescription.MedicationScheduler
import com.jarvis.assistant.sync.AutoSyncManager
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit
import javax.inject.Inject

/**
 * Foreground service that manages sensors (Bluetooth, accelerometer) and
 * delegates periodic sync work to [SyncWorker] via WorkManager for better
 * battery efficiency and OS scheduling.
 *
 * The foreground service still runs for:
 * - Bluetooth receiver (ParkingMemory)
 * - Notification channel management
 * - Medication alarm scheduling on start
 * - Foreground notification (keeps the service alive)
 *
 * Time-critical tasks (command queue flush, proactive alerts) run in a
 * lightweight coroutine loop (30s interval). All other sync operations
 * (spam sync, context detection, etc.) are handled by [SyncWorker] on a
 * 15-minute periodic schedule managed by WorkManager.
 */
@AndroidEntryPoint
class JarvisService : Service() {

    @Inject lateinit var channelManager: NotificationChannelManager
    @Inject lateinit var parkingMemory: ParkingMemory
    @Inject lateinit var medicationScheduler: MedicationScheduler
    @Inject lateinit var processor: CommandQueueProcessor
    @Inject lateinit var proactiveReceiver: ProactiveAlertReceiver
    @Inject lateinit var autoSyncManager: AutoSyncManager

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loopJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        channelManager.createChannels()
        startForeground(NOTIF_ID, buildNotification())
        val jarvisPrefs = getSharedPreferences("jarvis_prefs", Context.MODE_PRIVATE)
        if (jarvisPrefs.getBoolean("parking_memory", true)) {
            try {
                parkingMemory.registerBluetoothReceiver()
            } catch (e: Exception) {
                Log.w(TAG, "Failed to register Bluetooth receiver: ${e.message}")
            }
        }
        try {
            SpendSummaryWorker.enqueue(applicationContext)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to enqueue spend summary worker: ${e.message}")
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        enqueueSyncWorker()
        scheduleMedicationAlarms()
        recoverStaleCommands()
        startHighFrequencyLoop()
        // Start auto-sync: network monitoring, relay failover, adaptive intervals
        autoSyncManager.start()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        autoSyncManager.stop()
        parkingMemory.unregisterBluetoothReceiver()
        scope.cancel()
        super.onDestroy()
    }

    /**
     * Enqueue the periodic [SyncWorker] via WorkManager. Uses KEEP policy
     * to avoid duplicates if the service is restarted.
     */
    private fun enqueueSyncWorker() {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()

        // WorkManager enforces a 15-minute minimum for periodic work.
        // High-frequency tasks (command queue, proactive alerts) use a
        // coroutine loop in the foreground service instead.
        val syncRequest = PeriodicWorkRequestBuilder<SyncWorker>(
            15, TimeUnit.MINUTES,
        )
            .setConstraints(constraints)
            .build()

        WorkManager.getInstance(applicationContext).enqueueUniquePeriodicWork(
            SyncWorker.WORK_NAME,
            ExistingPeriodicWorkPolicy.KEEP,
            syncRequest,
        )
        Log.i(TAG, "SyncWorker enqueued (15-minute periodic, network required)")
    }

    /**
     * Schedule medication alarms on service start (ensures alarms survive
     * boot/restart).
     */
    private fun scheduleMedicationAlarms() {
        scope.launch {
            try {
                medicationScheduler.scheduleAllAlarms()
                Log.i(TAG, "Medication alarms scheduled on service start")
            } catch (e: Exception) {
                Log.w(TAG, "Failed to schedule medication alarms: ${e.message}")
            }
        }
    }

    /**
     * Recover commands stuck in 'sending' state from a prior crash/kill.
     * Called once at service start, NOT on every tick (to avoid resetting
     * legitimately in-flight sends).
     */
    private fun recoverStaleCommands() {
        scope.launch {
            try {
                processor.recoverStale()
                Log.i(TAG, "Stale command recovery completed")
            } catch (e: Exception) {
                Log.w(TAG, "Stale command recovery failed: ${e.message}")
            }
        }
    }

    /**
     * Lightweight coroutine loop for time-critical tasks that need sub-15-minute
     * frequency. WorkManager enforces a 15-minute minimum for periodic work,
     * so command queue flush and proactive alert checks run here instead.
     *
     * Synchronized to prevent duplicate loops when onStartCommand() is called
     * twice in quick succession (e.g., OS restart + pending intent).
     */
    @Synchronized
    private fun startHighFrequencyLoop() {
        if (loopJob?.isActive == true) return
        loopJob = scope.launch {
            while (true) {
                try {
                    processor.flushPending()
                } catch (e: Exception) {
                    Log.w(TAG, "Command queue flush error: ${e.message}")
                }
                try {
                    proactiveReceiver.checkAndPost()
                } catch (e: Exception) {
                    Log.w(TAG, "Proactive alert check error: ${e.message}")
                }
                delay(30_000L) // 30 seconds
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
        /** @deprecated Sync interval is now managed by WorkManager. Kept for API compat. */
        const val EXTRA_SYNC_INTERVAL = "sync_interval_ms"
        const val EXTRA_VOICE_COMMAND = "voice_command"
        private const val TAG = "JarvisService"
    }
}
