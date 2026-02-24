package com.jarvis.assistant.feature.prescription

import android.Manifest
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.jarvis.assistant.data.entity.MedicationLogEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * BroadcastReceiver triggered by AlarmManager when a medication dose is due.
 * Posts an URGENT notification on the jarvis_urgent channel (bypasses DND)
 * with Taken/Skip action buttons.
 *
 * Uses the EntryPointAccessors pattern (same as JarvisNotificationListenerService)
 * because BroadcastReceivers are not @AndroidEntryPoint-compatible for constructor
 * injection with Hilt.
 */
class DoseAlarmReceiver : BroadcastReceiver() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface DoseAlarmEntryPoint {
        fun medicationDao(): MedicationDao
        fun medicationLogDao(): MedicationLogDao
        fun medicationScheduler(): MedicationScheduler
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != MedicationScheduler.ACTION_DOSE_ALARM) return

        val medicationId = intent.getLongExtra(MedicationScheduler.EXTRA_MEDICATION_ID, -1)
        val medicationName = intent.getStringExtra(MedicationScheduler.EXTRA_MEDICATION_NAME) ?: "Medication"
        val dosage = intent.getStringExtra(MedicationScheduler.EXTRA_DOSAGE) ?: ""
        val scheduledTime = intent.getStringExtra(MedicationScheduler.EXTRA_SCHEDULED_TIME) ?: ""

        if (medicationId < 0) {
            Log.w(TAG, "Received dose alarm with invalid medication ID")
            return
        }

        Log.i(TAG, "Dose alarm fired: $medicationName ($dosage) at $scheduledTime")

        // Post URGENT notification (bypasses DND)
        postDoseNotification(context, medicationId, medicationName, dosage, scheduledTime)

        // Schedule next occurrence (tomorrow) and create pending log entry
        val pendingResult = goAsync()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope.launch {
            try {
                val entryPoint = EntryPointAccessors.fromApplication(
                    context.applicationContext,
                    DoseAlarmEntryPoint::class.java,
                )
                val logDao = entryPoint.medicationLogDao()
                val scheduler = entryPoint.medicationScheduler()

                // Create a pending log entry (status will be updated by action buttons)
                val todayDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                val existingLogs = logDao.getLogsForMedicationOnDate(medicationId, todayDate)
                val alreadyLogged = existingLogs.any { it.scheduledTime == scheduledTime }
                if (!alreadyLogged) {
                    logDao.insert(
                        MedicationLogEntity(
                            medicationId = medicationId,
                            medicationName = medicationName,
                            scheduledTime = scheduledTime,
                            status = "pending",
                            date = todayDate,
                        ),
                    )
                }

                // Reschedule for the next day
                scheduler.rescheduleForMedication(medicationId)
            } catch (e: Exception) {
                Log.e(TAG, "Error processing dose alarm", e)
            } finally {
                pendingResult.finish()
            }
        }
    }

    private fun postDoseNotification(
        context: Context,
        medicationId: Long,
        medicationName: String,
        dosage: String,
        scheduledTime: String,
    ) {
        if (!hasNotificationPermission(context)) return

        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // "Taken" action intent
        val takenIntent = Intent(context, DoseActionReceiver::class.java).apply {
            action = ACTION_DOSE_TAKEN
            putExtra(MedicationScheduler.EXTRA_MEDICATION_ID, medicationId)
            putExtra(MedicationScheduler.EXTRA_MEDICATION_NAME, medicationName)
            putExtra(MedicationScheduler.EXTRA_SCHEDULED_TIME, scheduledTime)
        }
        val takenPending = android.app.PendingIntent.getBroadcast(
            context,
            (medicationId * 1000 + 1).toInt(),
            takenIntent,
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT,
        )

        // "Skip" action intent
        val skipIntent = Intent(context, DoseActionReceiver::class.java).apply {
            action = ACTION_DOSE_SKIPPED
            putExtra(MedicationScheduler.EXTRA_MEDICATION_ID, medicationId)
            putExtra(MedicationScheduler.EXTRA_MEDICATION_NAME, medicationName)
            putExtra(MedicationScheduler.EXTRA_SCHEDULED_TIME, scheduledTime)
        }
        val skipPending = android.app.PendingIntent.getBroadcast(
            context,
            (medicationId * 1000 + 2).toInt(),
            skipIntent,
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val bodyText = if (dosage.isNotBlank()) {
            "Time to take $medicationName ($dosage)"
        } else {
            "Time to take $medicationName"
        }

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.URGENT.channelId,
        )
            .setContentTitle("Medication Reminder")
            .setContentText(bodyText)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .addAction(
                android.R.drawable.ic_menu_send,
                "Taken",
                takenPending,
            )
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel,
                "Skip",
                skipPending,
            )
            .build()

        val notificationId = medicationId.toInt() + NOTIFICATION_ID_OFFSET
        notificationManager.notify(notificationId, notification)
    }

    private fun hasNotificationPermission(context: Context): Boolean {
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
        private const val TAG = "DoseAlarmReceiver"
        const val ACTION_DOSE_TAKEN = "com.jarvis.assistant.DOSE_TAKEN"
        const val ACTION_DOSE_SKIPPED = "com.jarvis.assistant.DOSE_SKIPPED"
        const val NOTIFICATION_ID_OFFSET = 10000
    }
}

/**
 * Handles the "Taken" and "Skip" notification action buttons.
 * Updates the medication log and decrements pill count when dose is taken.
 */
class DoseActionReceiver : BroadcastReceiver() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface DoseActionEntryPoint {
        fun medicationDao(): MedicationDao
        fun medicationLogDao(): MedicationLogDao
    }

    override fun onReceive(context: Context, intent: Intent) {
        val medicationId = intent.getLongExtra(MedicationScheduler.EXTRA_MEDICATION_ID, -1)
        val medicationName = intent.getStringExtra(MedicationScheduler.EXTRA_MEDICATION_NAME) ?: ""
        val scheduledTime = intent.getStringExtra(MedicationScheduler.EXTRA_SCHEDULED_TIME) ?: ""

        if (medicationId < 0) return

        val isTaken = intent.action == DoseAlarmReceiver.ACTION_DOSE_TAKEN
        val status = if (isTaken) "taken" else "skipped"

        Log.i(TAG, "Dose action: $status for $medicationName at $scheduledTime")

        // Dismiss the notification
        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.cancel(medicationId.toInt() + DoseAlarmReceiver.NOTIFICATION_ID_OFFSET)

        val pendingResult = goAsync()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope.launch {
            try {
                val entryPoint = EntryPointAccessors.fromApplication(
                    context.applicationContext,
                    DoseActionEntryPoint::class.java,
                )
                val logDao = entryPoint.medicationLogDao()
                val medDao = entryPoint.medicationDao()

                val todayDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

                // Update existing pending log or insert new one
                val existingLogs = logDao.getLogsForMedicationOnDate(medicationId, todayDate)
                val pendingLog = existingLogs.firstOrNull {
                    it.scheduledTime == scheduledTime && it.status == "pending"
                }

                if (pendingLog != null) {
                    // Update the pending entry -- Room doesn't have a direct update-by-field,
                    // so we insert a new corrected entry (the pending one stays but is superseded)
                    logDao.insert(
                        MedicationLogEntity(
                            medicationId = medicationId,
                            medicationName = medicationName,
                            scheduledTime = scheduledTime,
                            takenAt = if (isTaken) System.currentTimeMillis() else 0L,
                            status = status,
                            date = todayDate,
                        ),
                    )
                } else {
                    logDao.insert(
                        MedicationLogEntity(
                            medicationId = medicationId,
                            medicationName = medicationName,
                            scheduledTime = scheduledTime,
                            takenAt = if (isTaken) System.currentTimeMillis() else 0L,
                            status = status,
                            date = todayDate,
                        ),
                    )
                }

                // Decrement pills if taken
                if (isTaken) {
                    medDao.decrementPills(medicationId)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error processing dose action", e)
            } finally {
                pendingResult.finish()
            }
        }
    }

    companion object {
        private const val TAG = "DoseActionReceiver"
    }
}
