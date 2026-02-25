package com.jarvis.assistant.feature.social

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.database.Cursor
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.entity.ContactContextEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.withTimeoutOrNull
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Displays a pre-call context notification with last conversation info
 * before a phone call starts.
 *
 * When an incoming or outgoing call is detected, [showPreCallCard] looks up
 * the contact in the local relationship database and posts an IMPORTANT
 * notification showing last call date, key topics, and notes.
 */
@Singleton
class PreCallCardManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val contactContextDao: ContactContextDao,
    private val callLogDao: CallLogDao,
    private val apiClient: JarvisApiClient,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Show a pre-call card notification for the given phone number.
     * Called by [CallStateReceiver] when a call starts (RINGING or OFFHOOK).
     */
    suspend fun showPreCallCard(phoneNumber: String) {
        // Check if feature is enabled
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_PRE_CALL_CARDS, true)) return

        if (!hasNotificationPermission()) return

        val normalizedNumber = normalizeNumber(phoneNumber)
        if (normalizedNumber.isBlank()) return

        val existingContact = contactContextDao.getByPhoneNumber(normalizedNumber)

        if (existingContact != null) {
            postContextCard(existingContact, normalizedNumber)
        } else {
            postNewContactCard(normalizedNumber)
        }
    }

    /**
     * Post a rich notification with existing contact context:
     * last call date, key topics, notes, and total calls.
     */
    private suspend fun postContextCard(
        contact: ContactContextEntity,
        normalizedNumber: String,
    ) {
        val bodyParts = mutableListOf<String>()

        // Last call date
        if (contact.lastCallDate.isNotBlank()) {
            bodyParts.add("Last call: ${contact.lastCallDate}")
        } else {
            bodyParts.add("First call")
        }

        // Key topics
        val topics = parseTopicsJson(contact.keyTopics)
        if (topics.isNotEmpty()) {
            bodyParts.add("Topics: ${topics.joinToString(", ")}")
        }

        // Last notes (truncated)
        if (contact.lastNotes.isNotBlank()) {
            val truncated = if (contact.lastNotes.length > 100) {
                contact.lastNotes.take(100) + "..."
            } else {
                contact.lastNotes
            }
            bodyParts.add("Notes: $truncated")
        }

        bodyParts.add("Total calls: ${contact.totalCalls}")

        // Try to get additional context from desktop brain (3-second timeout)
        val desktopContext = try {
            withTimeoutOrNull(3000L) {
                val response = apiClient.api().sendCommand(
                    CommandRequest(text = "What do you know about ${contact.contactName}?"),
                )
                if (response.ok && response.stdoutTail.isNotEmpty()) {
                    response.stdoutTail.joinToString(" ").take(200)
                } else {
                    null
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to fetch desktop brain context for pre-call card", e)
            null
        }

        if (!desktopContext.isNullOrBlank()) {
            bodyParts.add("Brain: $desktopContext")
        }

        val bodyText = bodyParts.joinToString("\n")
        val notifId = (normalizedNumber.hashCode() and 0x7FFFFFFF) + NOTIFICATION_ID_OFFSET

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.IMPORTANT.channelId,
        )
            .setContentTitle("Call: ${contact.contactName}")
            .setContentText(bodyParts.first())
            .setStyle(NotificationCompat.BigTextStyle().bigText(bodyText))
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setAutoCancel(true)
            .setTimeoutAfter(60_000)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()

        notificationManager.notify(notifId, notification)
        Log.i(TAG, "Pre-call card posted for ${contact.contactName}")
    }

    /**
     * Post a simpler notification for unknown contacts and create
     * a stub ContactContextEntity for future calls.
     */
    private suspend fun postNewContactCard(normalizedNumber: String) {
        val contactName = resolveContactName(normalizedNumber) ?: normalizedNumber
        val notifId = (normalizedNumber.hashCode() and 0x7FFFFFFF) + NOTIFICATION_ID_OFFSET

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.IMPORTANT.channelId,
        )
            .setContentTitle("Call: $contactName")
            .setContentText("No previous context recorded.")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setAutoCancel(true)
            .setTimeoutAfter(60_000)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()

        notificationManager.notify(notifId, notification)

        // Create a stub contact for future calls
        contactContextDao.upsert(
            ContactContextEntity(
                phoneNumber = normalizedNumber,
                contactName = contactName,
            ),
        )
        Log.i(TAG, "Pre-call card posted for new contact: $contactName")
    }

    /**
     * Normalize a phone number: strip non-digit characters and take
     * the last 10 digits (handles US numbers with/without country code).
     */
    fun normalizeNumber(number: String): String {
        val digitsOnly = number.filter { it.isDigit() }
        return if (digitsOnly.length >= 10) digitsOnly.takeLast(10) else digitsOnly
    }

    /**
     * Try to resolve a contact name from Android's ContactsContract
     * by phone number lookup.
     */
    private fun resolveContactName(phoneNumber: String): String? {
        return try {
            val uri = Uri.withAppendedPath(
                ContactsContract.PhoneLookup.CONTENT_FILTER_URI,
                Uri.encode(phoneNumber),
            )
            var cursor: Cursor? = null
            try {
                cursor = context.contentResolver.query(
                    uri,
                    arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME),
                    null, null, null,
                )
                if (cursor != null && cursor.moveToFirst()) {
                    cursor.getString(0)
                } else {
                    null
                }
            } finally {
                cursor?.close()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to resolve contact name", e)
            null
        }
    }

    /**
     * Parse a JSON array string of topics into a list.
     */
    private fun parseTopicsJson(json: String): List<String> {
        return try {
            if (json.isBlank() || json == "[]") {
                emptyList()
            } else {
                // Simple JSON array parsing without Gson dependency
                json.trim()
                    .removePrefix("[")
                    .removeSuffix("]")
                    .split(",")
                    .map { it.trim().removeSurrounding("\"") }
                    .filter { it.isNotBlank() }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse topics JSON", e)
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
        private const val TAG = "PreCallCardManager"
        const val NOTIFICATION_ID_OFFSET = 30000
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_PRE_CALL_CARDS = "pre_call_cards_enabled"
    }
}
