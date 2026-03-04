package com.jarvis.assistant.feature.automation

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Relationship autopilot — detects neglected important contacts and nudges
 * the user to reach out.
 *
 * Runs daily from SyncWorker. For each contact with importance >= 0.4,
 * calculates days since last contact. If the gap exceeds a threshold
 * (scaled by importance — more important = shorter threshold), posts
 * an IMPORTANT notification with:
 * - Who to call
 * - How long it's been
 * - What you last talked about (from contact context)
 * - One-tap "Call" action button
 *
 * This ensures important relationships don't silently decay.
 */
@Singleton
class RelationshipAutopilot @Inject constructor(
    @ApplicationContext private val context: Context,
    private val contactContextDao: ContactContextDao,
) {

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /** Track last run to avoid running more than once per day (persisted across process death). */
    private var lastRunDay: Long
        get() = prefs.getLong("last_run_day", 0L)
        set(value) = prefs.edit().putLong("last_run_day", value).apply()

    /**
     * Check for neglected contacts and post reminder notifications.
     * Called from SyncWorker, but self-limits to once per day.
     */
    suspend fun checkNeglectedContacts() {
        if (!prefs.getBoolean(KEY_ENABLED, true)) return

        // Run at most once per day
        val today = System.currentTimeMillis() / TimeUnit.DAYS.toMillis(1)
        if (today <= lastRunDay) return
        lastRunDay = today

        val baseThresholdDays = prefs.getInt(KEY_THRESHOLD_DAYS, DEFAULT_THRESHOLD_DAYS)
        val now = System.currentTimeMillis()

        try {
            val contacts = contactContextDao.getAll()
            var notifCount = 0

            for (contact in contacts) {
                if (contact.importance < 0.4f) continue
                if (contact.lastCallTimestamp <= 0) continue

                val daysSince = TimeUnit.MILLISECONDS.toDays(now - contact.lastCallTimestamp).toInt()

                // Scale threshold by importance:
                // importance=1.0 → threshold * 0.5 (call very often)
                // importance=0.4 → threshold * 0.8
                val adjustedThreshold = (baseThresholdDays * (1.0 - contact.importance * 0.5)).toInt()
                    .coerceAtLeast(7)

                if (daysSince < adjustedThreshold) continue
                if (notifCount >= MAX_NOTIFICATIONS) break

                val name = contact.contactName.ifBlank { "***${contact.phoneNumber.takeLast(4)}" }
                val bodyParts = mutableListOf("It's been $daysSince days since you last talked")

                // Add context from last interaction
                if (contact.lastNotes.isNotBlank()) {
                    bodyParts.add("Last discussed: ${contact.lastNotes.take(80)}")
                }

                // Add key topics
                try {
                    val topics = com.google.gson.Gson().fromJson(
                        contact.keyTopics,
                        Array<String>::class.java,
                    )
                    if (topics != null && topics.isNotEmpty()) {
                        bodyParts.add("Topics: ${topics.take(3).joinToString(", ")}")
                    }
                } catch (_: Exception) {}

                val body = bodyParts.joinToString("\n")

                // Build "Call" action button
                val callIntent = Intent(Intent.ACTION_DIAL).apply {
                    data = Uri.parse("tel:${contact.phoneNumber}")
                }
                val callPending = PendingIntent.getActivity(
                    context,
                    contact.id.toInt(),
                    callIntent,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                )

                val notification = NotificationCompat.Builder(
                    context,
                    NotificationPriority.IMPORTANT.channelId,
                )
                    .setSmallIcon(R.drawable.ic_launcher_foreground)
                    .setContentTitle("Reconnect with $name")
                    .setContentText("It's been $daysSince days")
                    .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                    .setAutoCancel(true)
                    .setGroup("jarvis_relationship")
                    .addAction(
                        android.R.drawable.ic_menu_call,
                        "Call",
                        callPending,
                    )
                    .setTimeoutAfter(12 * 60 * 60 * 1000L) // Dismiss after 12 hours
                    .build()

                notificationManager.notify(
                    NOTIFICATION_ID_BASE + contact.id.toInt(),
                    notification,
                )
                notifCount++

                Log.i(TAG, "Relationship nudge: $name ($daysSince days, importance=${contact.importance})")
            }

            if (notifCount > 0) {
                Log.i(TAG, "Posted $notifCount relationship autopilot notifications")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Relationship autopilot failed: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "RelationshipAuto"
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_ENABLED = "relationship_autopilot_enabled"
        const val KEY_THRESHOLD_DAYS = "relationship_neglect_days"
        const val DEFAULT_THRESHOLD_DAYS = 14
        private const val MAX_NOTIFICATIONS = 3
        private const val NOTIFICATION_ID_BASE = 7000
    }
}
