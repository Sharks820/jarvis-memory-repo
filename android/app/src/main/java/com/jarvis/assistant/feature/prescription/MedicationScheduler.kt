package com.jarvis.assistant.feature.prescription

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import com.jarvis.assistant.data.dao.MedicationDao
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Calendar
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
                val requestCode = (medication.id * 100 + index).toInt()
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
        }
        Log.i(TAG, "Scheduled alarms for ${medications.size} active medications")
    }

    /**
     * Cancel all pending alarms for active medications.
     */
    suspend fun cancelAllAlarms() {
        val medications = medicationDao.getActiveMedications()
        for (medication in medications) {
            val times = parseTimes(medication.scheduledTimes)
            times.forEachIndexed { index, time ->
                val requestCode = (medication.id * 100 + index).toInt()
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
            val requestCode = (medication.id * 100 + index).toInt()
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
                val requestCode = (medication.id * 100 + index).toInt()
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
    }
}
