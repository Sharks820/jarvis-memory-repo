package com.jarvis.assistant.feature.social

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
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Surfaces birthday, anniversary, and neglected connection alerts.
 *
 * Called periodically (once per day) from [JarvisService] sync loop.
 * Checks local relationship database for upcoming dates and contacts
 * the user hasn't spoken with in a configurable number of days.
 * Also queries the desktop brain for social calendar events.
 */
@Singleton
class RelationshipAlertEngine @Inject constructor(
    @ApplicationContext private val context: Context,
    private val contactContextDao: ContactContextDao,
    private val apiClient: JarvisApiClient,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * Check for relationship alerts: birthdays, anniversaries, and neglected connections.
     * Called once per day from JarvisService.
     */
    suspend fun checkRelationshipAlerts() {
        if (!prefs.getBoolean(KEY_RELATIONSHIP_ALERTS, true)) return
        if (!hasNotificationPermission()) return

        checkBirthdays()
        checkAnniversaries()
        checkNeglectedConnections()
        syncDesktopSocialGraph()
    }

    /**
     * Check for contacts with birthdays today or tomorrow.
     */
    private suspend fun checkBirthdays() {
        if (!prefs.getBoolean(KEY_BIRTHDAY_REMINDERS, true)) return

        val contacts = contactContextDao.getContactsWithBirthdays()
        val today = getTodayMMDD()
        val tomorrow = getTomorrowMMDD()
        val year = Calendar.getInstance().get(Calendar.YEAR).toString()

        for (contact in contacts) {
            val alertKey = "birthday_alerted_${contact.id}_$year"
            if (prefs.getBoolean(alertKey, false)) continue

            val birthdayMMDD = extractMMDD(contact.birthday) ?: continue
            val when_ = when (birthdayMMDD) {
                today -> "today"
                tomorrow -> "tomorrow"
                else -> continue
            }

            val notifId = (contact.id.toInt() and 0x7FFFFFFF) + BIRTHDAY_NOTIFICATION_OFFSET

            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.IMPORTANT.channelId,
            )
                .setContentTitle("Birthday Alert")
                .setContentText("${contact.contactName}'s birthday is $when_!")
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .build()

            notificationManager.notify(notifId, notification)
            prefs.edit().putBoolean(alertKey, true).apply()
            Log.i(TAG, "Birthday alert posted for ${contact.contactName}")
        }
    }

    /**
     * Check for contacts with anniversaries today or tomorrow.
     */
    private suspend fun checkAnniversaries() {
        if (!prefs.getBoolean(KEY_ANNIVERSARY_REMINDERS, true)) return

        val contacts = contactContextDao.getContactsWithAnniversaries()
        val today = getTodayMMDD()
        val tomorrow = getTomorrowMMDD()
        val year = Calendar.getInstance().get(Calendar.YEAR).toString()

        for (contact in contacts) {
            val alertKey = "anniversary_alerted_${contact.id}_$year"
            if (prefs.getBoolean(alertKey, false)) continue

            val anniversaryMMDD = extractMMDD(contact.anniversary) ?: continue
            val when_ = when (anniversaryMMDD) {
                today -> "today"
                tomorrow -> "tomorrow"
                else -> continue
            }

            val notifId = (contact.id.toInt() and 0x7FFFFFFF) + ANNIVERSARY_NOTIFICATION_OFFSET

            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.IMPORTANT.channelId,
            )
                .setContentTitle("Anniversary")
                .setContentText(
                    "Your anniversary with ${contact.contactName} is $when_!",
                )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .build()

            notificationManager.notify(notifId, notification)
            prefs.edit().putBoolean(alertKey, true).apply()
            Log.i(TAG, "Anniversary alert posted for ${contact.contactName}")
        }
    }

    /**
     * Check for important contacts the user hasn't spoken with recently.
     * Uses the configurable neglected threshold (default 30 days).
     */
    private suspend fun checkNeglectedConnections() {
        if (!prefs.getBoolean(KEY_NEGLECTED_ALERTS, true)) return

        val thresholdDays = prefs.getInt(KEY_NEGLECTED_THRESHOLD_DAYS, 30)
        val cutoff = System.currentTimeMillis() - thresholdDays * 24L * 60 * 60 * 1000
        val todayDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

        val neglected = contactContextDao.getNeglectedContacts(cutoff)
        var alertCount = 0

        for (contact in neglected) {
            if (alertCount >= MAX_NEGLECTED_ALERTS_PER_DAY) break

            val alertKey = "neglected_alerted_${contact.id}_$todayDate"
            if (prefs.getBoolean(alertKey, false)) continue

            val daysSinceLastCall = if (contact.lastCallTimestamp > 0) {
                ((System.currentTimeMillis() - contact.lastCallTimestamp) /
                    (24L * 60 * 60 * 1000)).toInt()
            } else {
                thresholdDays
            }

            val notifId = (contact.id.toInt() and 0x7FFFFFFF) + NEGLECTED_NOTIFICATION_OFFSET

            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.ROUTINE.channelId,
            )
                .setContentTitle("Stay in touch")
                .setContentText(
                    "You haven't spoken with ${contact.contactName} " +
                        "in $daysSinceLastCall days.",
                )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setAutoCancel(true)
                .build()

            notificationManager.notify(notifId, notification)
            prefs.edit().putBoolean(alertKey, true).apply()
            alertCount++
            Log.i(
                TAG,
                "Neglected alert posted for ${contact.contactName} " +
                    "($daysSinceLastCall days)",
            )
        }
    }

    /**
     * Query desktop brain for social calendar events and supplement
     * local birthday/anniversary data.
     */
    private suspend fun syncDesktopSocialGraph() {
        try {
            val response = apiClient.api().sendCommand(
                CommandRequest(
                    text = "Check social calendar for upcoming birthdays and anniversaries",
                ),
            )
            if (response.ok && response.stdoutTail.isNotEmpty()) {
                Log.i(
                    TAG,
                    "Desktop social graph response: ${response.stdoutTail.joinToString(" ")}",
                )
                // Future: parse response to supplement local data
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to sync desktop social graph", e)
        }
    }

    /**
     * Calculate importance based on call frequency and recency.
     */
    suspend fun calculateImportance(contactId: Long): Float {
        val contact = contactContextDao.getByPhoneNumber("") // unused fallback
        // Get by ID is not in DAO, so we use getAll and filter
        val allContacts = contactContextDao.getAll()
        val contactEntity = allContacts.firstOrNull { it.id == contactId } ?: return 0.0f

        val monthsSinceCreated = maxOf(
            1.0f,
            (System.currentTimeMillis() - contactEntity.createdAt) /
                (30L * 24 * 60 * 60 * 1000).toFloat(),
        )
        val callFrequency = (contactEntity.totalCalls / monthsSinceCreated).coerceAtMost(1.0f)

        val daysSinceLastCall = if (contactEntity.lastCallTimestamp > 0) {
            (System.currentTimeMillis() - contactEntity.lastCallTimestamp) /
                (24L * 60 * 60 * 1000).toFloat()
        } else {
            90.0f
        }
        val recency = (1.0f - daysSinceLastCall / 90.0f).coerceIn(0.0f, 1.0f)

        return (callFrequency * 0.4f + recency * 0.6f).coerceIn(0.0f, 1.0f)
    }

    /**
     * Extract MM-dd from various date formats stored in contact records.
     * Handles: "MM-dd", "yyyy-MM-dd", "MM/dd/yyyy", "MM/dd".
     * Returns null if the format is unrecognised.
     */
    private fun extractMMDD(dateStr: String): String? {
        return try {
            when {
                dateStr.matches(Regex("\\d{2}-\\d{2}")) -> dateStr // Already MM-dd
                dateStr.matches(Regex("\\d{4}-\\d{2}-\\d{2}")) -> dateStr.substring(5) // yyyy-MM-dd -> MM-dd
                dateStr.matches(Regex("\\d{2}/\\d{2}/\\d{4}")) -> {
                    // MM/dd/yyyy -> MM-dd
                    val parts = dateStr.split("/")
                    "${parts[0]}-${parts[1]}"
                }
                dateStr.matches(Regex("\\d{2}/\\d{2}")) -> {
                    // MM/dd -> MM-dd
                    dateStr.replace("/", "-")
                }
                else -> null
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse date string: $dateStr", e)
            null
        }
    }

    private fun getTodayMMDD(): String {
        return SimpleDateFormat("MM-dd", Locale.US).format(Date())
    }

    private fun getTomorrowMMDD(): String {
        val cal = Calendar.getInstance()
        cal.add(Calendar.DAY_OF_YEAR, 1)
        return SimpleDateFormat("MM-dd", Locale.US).format(cal.time)
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
        private const val TAG = "RelationshipAlerts"
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_RELATIONSHIP_ALERTS = "relationship_alerts_enabled"
        const val KEY_BIRTHDAY_REMINDERS = "birthday_reminders_enabled"
        const val KEY_ANNIVERSARY_REMINDERS = "anniversary_reminders_enabled"
        const val KEY_NEGLECTED_ALERTS = "neglected_alerts_enabled"
        const val KEY_NEGLECTED_THRESHOLD_DAYS = "neglected_threshold_days"
        const val BIRTHDAY_NOTIFICATION_OFFSET = 50000
        const val ANNIVERSARY_NOTIFICATION_OFFSET = 51000
        const val NEGLECTED_NOTIFICATION_OFFSET = 52000
        const val MAX_NEGLECTED_ALERTS_PER_DAY = 2
    }
}
