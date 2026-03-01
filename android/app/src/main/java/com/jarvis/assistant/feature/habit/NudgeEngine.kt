package com.jarvis.assistant.feature.habit

import android.Manifest
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import com.jarvis.assistant.data.entity.NudgeLogEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Decides when and how to deliver nudges based on detected patterns.
 *
 * Called every 5 minutes from [JarvisService]. For each active, unsuppressed pattern,
 * checks if the current day/time matches the trigger and delivers a ROUTINE notification
 * with Done/Dismiss action buttons.
 */
@Singleton
class NudgeEngine @Inject constructor(
    private val habitDao: HabitDao,
    private val nudgeLogDao: NudgeLogDao,
    private val responseTracker: NudgeResponseTracker,
    private val builtInNudges: BuiltInNudges,
    @ApplicationContext private val context: Context,
) {

    /**
     * Called periodically from JarvisService (every 5 minutes).
     *
     * 1. Gets current day-of-week and time
     * 2. Queries active, unsuppressed patterns
     * 3. For matching patterns, delivers nudge notifications
     */
    suspend fun checkAndDeliver() {
        val now = LocalDateTime.now()
        val currentDayOfWeek = now.dayOfWeek.value // 1=Mon..7=Sun
        val currentHour = now.hour
        val currentMinute = now.minute
        val today = now.toLocalDate().format(DateTimeFormatter.ISO_LOCAL_DATE)

        // Expire stale nudges
        responseTracker.expireStaleNudges()

        // Get all active patterns (includes built-in and detected)
        val activePatterns = habitDao.getActivePatterns()

        // Get today's already-delivered nudges to avoid duplicates
        val todayLogs = nudgeLogDao.getLogsForDate(today)
        val deliveredPatternIds = todayLogs.map { it.patternId }.toSet()

        for (pattern in activePatterns) {
            // Skip if confidence is below threshold
            if (pattern.confidence < MIN_NUDGE_CONFIDENCE) continue

            // Skip if already delivered today
            if (pattern.id in deliveredPatternIds) continue

            // Check if current day is in trigger days
            val triggerDays = parseTriggerDays(pattern.triggerDays)
            if (currentDayOfWeek !in triggerDays) continue

            // Check if current time is within 15 minutes of trigger time
            if (!isWithinTimeWindow(
                    currentHour, currentMinute,
                    pattern.triggerHour, pattern.triggerMinute,
                    TIME_WINDOW_MINUTES,
                )
            ) continue

            // Check adaptive suppression
            if (responseTracker.shouldSuppress(pattern.id)) continue

            // All checks pass -- deliver the nudge
            deliverNudge(pattern.id, pattern.label, pattern.description, today)
        }
    }

    private suspend fun deliverNudge(
        patternId: Long,
        label: String,
        description: String,
        date: String,
    ) {
        // Check notification permission BEFORE logging delivery
        if (!hasNotificationPermission()) return

        // Insert nudge log entry only after confirming we can actually deliver
        val logId = nudgeLogDao.insert(
            NudgeLogEntity(
                patternId = patternId,
                patternLabel = label,
                nudgeText = description,
                deliveredAt = System.currentTimeMillis(),
                date = date,
            ),
        )

        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // "Done" action intent
        val doneIntent = Intent(context, NudgeActionReceiver::class.java).apply {
            action = ACTION_NUDGE_ACTED
            putExtra(EXTRA_NUDGE_LOG_ID, logId)
            putExtra(EXTRA_PATTERN_ID, patternId)
        }
        val donePending = PendingIntent.getBroadcast(
            context,
            ((patternId.toInt() * 100 + 1) and 0x7FFFFFFF),
            doneIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        // "Dismiss" action intent
        val dismissIntent = Intent(context, NudgeActionReceiver::class.java).apply {
            action = ACTION_NUDGE_DISMISSED
            putExtra(EXTRA_NUDGE_LOG_ID, logId)
            putExtra(EXTRA_PATTERN_ID, patternId)
        }
        val dismissPending = PendingIntent.getBroadcast(
            context,
            ((patternId.toInt() * 100 + 2) and 0x7FFFFFFF),
            dismissIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.ROUTINE.channelId,
        )
            .setContentTitle(label)
            .setContentText(description)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setAutoCancel(true)
            .addAction(
                android.R.drawable.ic_menu_send,
                "Done",
                donePending,
            )
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel,
                "Dismiss",
                dismissPending,
            )
            .build()

        val notificationId = ((patternId.toInt() + NOTIFICATION_ID_OFFSET) and 0x7FFFFFFF)
        notificationManager.notify(notificationId, notification)
        Log.i(TAG, "Delivered nudge: $label (logId=$logId)")
    }

    private fun parseTriggerDays(triggerDays: String): List<Int> {
        return try {
            triggerDays.trim('[', ']')
                .split(",")
                .mapNotNull { it.trim().toIntOrNull() }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse trigger days: $triggerDays", e)
            emptyList()
        }
    }

    private fun isWithinTimeWindow(
        currentHour: Int,
        currentMinute: Int,
        triggerHour: Int,
        triggerMinute: Int,
        windowMinutes: Int,
    ): Boolean {
        val currentTotalMinutes = currentHour * 60 + currentMinute
        val triggerTotalMinutes = triggerHour * 60 + triggerMinute
        val diff = kotlin.math.abs(currentTotalMinutes - triggerTotalMinutes)
        // Handle midnight wrap-around (e.g., trigger 23:50, current 00:05)
        val wrappedDiff = kotlin.math.min(diff, 1440 - diff)
        return wrappedDiff <= windowMinutes
    }

    private fun hasNotificationPermission(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            true
        }
    }

    companion object {
        private const val TAG = "NudgeEngine"
        const val ACTION_NUDGE_ACTED = "com.jarvis.assistant.NUDGE_ACTED"
        const val ACTION_NUDGE_DISMISSED = "com.jarvis.assistant.NUDGE_DISMISSED"
        const val EXTRA_NUDGE_LOG_ID = "nudge_log_id"
        const val EXTRA_PATTERN_ID = "pattern_id"
        const val NOTIFICATION_ID_OFFSET = 25000
        private const val TIME_WINDOW_MINUTES = 15
        private const val MIN_NUDGE_CONFIDENCE = 0.6f
    }
}

/**
 * Handles Done/Dismiss notification action buttons for habit nudges.
 *
 * Uses the EntryPointAccessors pattern (same as DoseAlarmReceiver) because
 * BroadcastReceivers are not @AndroidEntryPoint-compatible for Hilt.
 */
class NudgeActionReceiver : BroadcastReceiver() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface NudgeActionEntryPoint {
        fun nudgeResponseTracker(): NudgeResponseTracker
    }

    override fun onReceive(context: Context, intent: Intent) {
        val logId = intent.getLongExtra(NudgeEngine.EXTRA_NUDGE_LOG_ID, -1)
        val patternId = intent.getLongExtra(NudgeEngine.EXTRA_PATTERN_ID, -1)

        if (logId < 0) {
            Log.w(TAG, "Received nudge action with invalid log ID")
            return
        }

        val isActed = intent.action == NudgeEngine.ACTION_NUDGE_ACTED
        val response = if (isActed) "acted" else "dismissed"

        Log.i(TAG, "Nudge action: $response for logId=$logId, patternId=$patternId")

        // Dismiss the notification
        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (patternId >= 0) {
            notificationManager.cancel(((patternId.toInt() + NudgeEngine.NOTIFICATION_ID_OFFSET) and 0x7FFFFFFF))
        }

        // Record response asynchronously
        val pendingResult = goAsync()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope.launch {
            try {
                val entryPoint = EntryPointAccessors.fromApplication(
                    context.applicationContext,
                    NudgeActionEntryPoint::class.java,
                )
                val tracker = entryPoint.nudgeResponseTracker()
                tracker.recordResponse(logId, response)
            } catch (e: Exception) {
                Log.e(TAG, "Error recording nudge response", e)
            } finally {
                pendingResult.finish()
            }
        }
    }

    companion object {
        private const val TAG = "NudgeActionReceiver"
    }
}
