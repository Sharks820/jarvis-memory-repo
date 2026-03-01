package com.jarvis.assistant.feature.prescription

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.feature.notifications.NotificationChannelManager
import com.jarvis.assistant.feature.notifications.NotificationPriority
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import dagger.hilt.android.qualifiers.ApplicationContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks remaining pill counts and posts proactive refill reminders
 * when a medication's supply drops below its configured threshold.
 *
 * Also handles syncing medication data to the desktop brain via
 * the /command endpoint.
 */
@Singleton
class RefillTracker @Inject constructor(
    private val medicationDao: MedicationDao,
    private val channelManager: NotificationChannelManager,
    @ApplicationContext private val context: Context,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    private val refillPrefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private val gson = Gson()

    /**
     * Check all active medications and post refill reminders for any
     * whose remaining supply is below the configured threshold.
     *
     * Only reminds once per day per medication (tracked via SharedPreferences).
     */
    suspend fun checkRefills() {
        val medications = medicationDao.getActiveMedications()
        val todayKey = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

        for (medication in medications) {
            val times = parseTimes(medication.scheduledTimes)
            val dosesPerDay = times.size.coerceAtLeast(1)
            val daysRemaining = if (dosesPerDay > 0) {
                medication.pillsRemaining / dosesPerDay
            } else {
                medication.pillsRemaining
            }

            if (daysRemaining <= medication.refillReminderDays) {
                val lastRemindedKey = "refill_last_reminded_${medication.id}"
                val lastReminded = refillPrefs.getString(lastRemindedKey, "")

                if (lastReminded != todayKey) {
                    postRefillNotification(
                        medication.name,
                        medication.pillsRemaining,
                        daysRemaining,
                        medication.id,
                    )
                    refillPrefs.edit().putString(lastRemindedKey, todayKey).apply()
                    Log.i(
                        TAG,
                        "Refill reminder posted for ${medication.name}: " +
                            "${medication.pillsRemaining} pills (~$daysRemaining days)",
                    )
                }
            }
        }
    }

    /**
     * Sync medication data to the desktop brain via the /command endpoint.
     * Called after medication changes (add, edit, refill).
     */
    suspend fun syncToDesktop(apiClient: JarvisApiClient) {
        try {
            val medications = medicationDao.getActiveMedications()
            for (medication in medications) {
                val commandText = "Jarvis, remember medication: ${medication.name} " +
                    "${medication.dosage} ${medication.frequency}, " +
                    "${medication.pillsRemaining} pills remaining"
                apiClient.api().sendCommand(
                    CommandRequest(text = commandText, execute = false),
                )
            }
            Log.i(TAG, "Synced ${medications.size} medications to desktop")
        } catch (e: Exception) {
            Log.w(TAG, "Failed to sync medications to desktop: ${e.message}")
        }
    }

    private fun postRefillNotification(
        medicationName: String,
        pillsRemaining: Int,
        daysRemaining: Int,
        medicationId: Long,
    ) {
        if (!hasNotificationPermission()) return

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.IMPORTANT.channelId,
        )
            .setContentTitle("Refill Reminder: $medicationName")
            .setContentText("$pillsRemaining pills remaining (~$daysRemaining days)")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()

        val notificationId = ((medicationId.toInt() + REFILL_NOTIFICATION_OFFSET) and 0x7FFFFFFF)
        notificationManager.notify(notificationId, notification)
    }

    private fun parseTimes(scheduledTimes: String): List<String> {
        return try {
            val type = object : TypeToken<List<String>>() {}.type
            gson.fromJson(scheduledTimes, type) ?: emptyList()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse scheduled times JSON", e)
            emptyList()
        }
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
        private const val TAG = "RefillTracker"
        private const val PREFS_NAME = "jarvis_refill_prefs"
        private const val REFILL_NOTIFICATION_OFFSET = 15000
    }
}
