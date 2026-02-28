package com.jarvis.assistant.feature.prescription

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationManager
import android.app.PendingIntent
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
import com.jarvis.assistant.data.entity.MedicationEntity
import com.jarvis.assistant.data.entity.MedicationLogEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import dagger.hilt.android.qualifiers.ApplicationContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Schedules EXACT_ALARM dose reminders via [AlarmManager] for each active
 * medication's scheduled times. Alarms fire even in Doze mode via
 * [AlarmManager.setExactAndAllowWhileIdle].
 */
@Singleton
class MedicationScheduler @Inject constructor(
    @ApplicationContext private val context: Context,
    private val medicationDao: MedicationDao,
    private val medicationLogDao: MedicationLogDao,
) {

    private val alarmManager: AlarmManager by lazy {
        context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
    }

    private val gson = Gson()

    /**
     * Schedule alarms for every active medication's dose times.
     * Call once on service start and after boot.
     */
    suspend fun scheduleAllAlarms() {
        if (!canScheduleExactAlarms()) {
            Log.w(TAG, "Cannot schedule exact alarms -- permission not granted")
            return
        }

        val medications = medicationDao.getActiveMedications()
        val now = System.currentTimeMillis()
        for (medication in medications) {
            val times = parseTimes(medication.scheduledTimes)
            times.forEachIndexed { index, time ->
                val triggerMs = getNextAlarmTimeMs(time)
                val intent = buildAlarmIntent(
                    medicationId = medication.id,
                    medicationName = medication.name,
                    dosage = medication.dosage,
                    scheduledTime = time,
                )
                val requestCode = ((medication.id.toInt() * 100 + index) and 0x7FFFFFFF)
                val pendingIntent = PendingIntent.getBroadcast(
                    context,
                    requestCode,
                    intent,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                )
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    triggerMs,
                    pendingIntent,
                )
                Log.d(TAG, "Scheduled alarm for ${medication.name} at $time (trigger=$triggerMs)")
            }

            // Check for missed doses after process death / reboot
            checkAndNotifyMissedDoses(medication, now)
        }
        Log.i(TAG, "Scheduled alarms for ${medications.size} active medications")
    }

    /**
     * Check if any of today's scheduled dose times have already passed without
     * a corresponding log entry, and fire an immediate notification for each missed dose.
     * This catches doses missed during extended process death or device reboot.
     */
    private suspend fun checkAndNotifyMissedDoses(medication: MedicationEntity, now: Long) {
        val todayDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date(now))
        val todayLogs = medicationLogDao.getLogsForMedicationOnDate(medication.id, todayDate)
        val times = parseTimes(medication.scheduledTimes)

        for (time in times) {
            val scheduledMs = getTodayTimeMs(time)
            // Only consider doses whose scheduled time has passed
            if (scheduledMs >= now) continue

            // Check if there is already a log entry for this dose time today
            val alreadyLogged = todayLogs.any { it.scheduledTime == time }
            if (alreadyLogged) continue

            // Create a "missed" log entry
            medicationLogDao.insert(
                MedicationLogEntity(
                    medicationId = medication.id,
                    medicationName = medication.name,
                    scheduledTime = time,
                    status = "missed",
                    date = todayDate,
                ),
            )

            // Post an urgent notification for the missed dose
            postMissedDoseNotification(medication, time)
            Log.w(TAG, "Missed dose detected: ${medication.name} at $time")
        }
    }

    /**
     * Post an URGENT notification for a missed medication dose.
     */
    private fun postMissedDoseNotification(medication: MedicationEntity, scheduledTime: String) {
        if (!hasNotificationPermission()) return

        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val bodyText = if (medication.dosage.isNotBlank()) {
            "Missed dose: ${medication.name} (${medication.dosage}) was due at $scheduledTime"
        } else {
            "Missed dose: ${medication.name} was due at $scheduledTime"
        }

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.URGENT.channelId,
        )
            .setContentTitle("Missed Medication")
            .setContentText(bodyText)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()

        val notificationId = ((medication.id.toInt() * 100 + MISSED_DOSE_NOTIFICATION_OFFSET) and 0x7FFFFFFF)
        notificationManager.notify(notificationId, notification)
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

    /**
     * Get the epoch millis for a given HH:mm time today (not rolling to tomorrow).
     */
    private fun getTodayTimeMs(hourMinute: String): Long {
        val parts = hourMinute.split(":")
        val hour = parts.getOrNull(0)?.toIntOrNull() ?: 8
        val minute = parts.getOrNull(1)?.toIntOrNull() ?: 0

        return Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis
    }

    /**
     * Cancel all pending alarms for active medications.
     */
    suspend fun cancelAllAlarms() {
        val medications = medicationDao.getActiveMedications()
        for (medication in medications) {
            val times = parseTimes(medication.scheduledTimes)
            times.forEachIndexed { index, time ->
                val requestCode = ((medication.id.toInt() * 100 + index) and 0x7FFFFFFF)
                val intent = buildAlarmIntent(
                    medicationId = medication.id,
                    medicationName = medication.name,
                    dosage = medication.dosage,
                    scheduledTime = time,
                )
                val pendingIntent = PendingIntent.getBroadcast(
                    context,
                    requestCode,
                    intent,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_NO_CREATE,
                )
                pendingIntent?.let { alarmManager.cancel(it) }
            }
        }
        Log.i(TAG, "Cancelled all medication alarms")
    }

    /**
     * Cancel and re-create alarms for a single medication (call after edit/add).
     */
    suspend fun rescheduleForMedication(medicationId: Long) {
        val medication = medicationDao.getById(medicationId) ?: return
        val times = parseTimes(medication.scheduledTimes)

        // Cancel existing
        times.forEachIndexed { index, time ->
            val requestCode = ((medication.id.toInt() * 100 + index) and 0x7FFFFFFF)
            val intent = buildAlarmIntent(
                medicationId = medication.id,
                medicationName = medication.name,
                dosage = medication.dosage,
                scheduledTime = time,
            )
            val pendingIntent = PendingIntent.getBroadcast(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_NO_CREATE,
            )
            pendingIntent?.let { alarmManager.cancel(it) }
        }

        // Re-schedule if active
        if (medication.isActive && canScheduleExactAlarms()) {
            times.forEachIndexed { index, time ->
                val triggerMs = getNextAlarmTimeMs(time)
                val requestCode = ((medication.id.toInt() * 100 + index) and 0x7FFFFFFF)
                val intent = buildAlarmIntent(
                    medicationId = medication.id,
                    medicationName = medication.name,
                    dosage = medication.dosage,
                    scheduledTime = time,
                )
                val pendingIntent = PendingIntent.getBroadcast(
                    context,
                    requestCode,
                    intent,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                )
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    triggerMs,
                    pendingIntent,
                )
            }
        }
        Log.d(TAG, "Rescheduled alarms for medication $medicationId")
    }

    /**
     * Parse "HH:mm" and return the next occurrence as epoch millis.
     * If the time has already passed today, returns tomorrow's occurrence.
     */
    private fun getNextAlarmTimeMs(hourMinute: String): Long {
        val parts = hourMinute.split(":")
        val hour = parts.getOrNull(0)?.toIntOrNull() ?: 8
        val minute = parts.getOrNull(1)?.toIntOrNull() ?: 0

        val calendar = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }

        // If the time has already passed today, schedule for tomorrow
        if (calendar.timeInMillis <= System.currentTimeMillis()) {
            calendar.add(Calendar.DAY_OF_YEAR, 1)
        }

        return calendar.timeInMillis
    }

    private fun buildAlarmIntent(
        medicationId: Long,
        medicationName: String,
        dosage: String,
        scheduledTime: String,
    ): Intent {
        return Intent(context, DoseAlarmReceiver::class.java).apply {
            action = ACTION_DOSE_ALARM
            putExtra(EXTRA_MEDICATION_ID, medicationId)
            putExtra(EXTRA_MEDICATION_NAME, medicationName)
            putExtra(EXTRA_DOSAGE, dosage)
            putExtra(EXTRA_SCHEDULED_TIME, scheduledTime)
        }
    }

    private fun parseTimes(scheduledTimes: String): List<String> {
        return try {
            val type = object : TypeToken<List<String>>() {}.type
            gson.fromJson(scheduledTimes, type) ?: emptyList()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse scheduledTimes: $scheduledTimes")
            emptyList()
        }
    }

    private fun canScheduleExactAlarms(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            alarmManager.canScheduleExactAlarms()
        } else {
            true // Pre-Android 12 doesn't require the permission
        }
    }

    companion object {
        private const val TAG = "MedicationScheduler"
        const val ACTION_DOSE_ALARM = "com.jarvis.assistant.DOSE_ALARM"
        const val EXTRA_MEDICATION_ID = "medication_id"
        const val EXTRA_MEDICATION_NAME = "medication_name"
        const val EXTRA_DOSAGE = "dosage"
        const val EXTRA_SCHEDULED_TIME = "scheduled_time"
        private const val MISSED_DOSE_NOTIFICATION_OFFSET = 20000
    }
}
