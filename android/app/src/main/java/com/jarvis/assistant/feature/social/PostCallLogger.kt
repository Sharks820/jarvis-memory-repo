package com.jarvis.assistant.feature.social

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
import androidx.core.app.RemoteInput
import androidx.core.content.ContextCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.entity.CallLogEntity
import com.jarvis.assistant.data.entity.ContactContextEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Prompts the user to log conversation context after a call ends.
 *
 * Posts a ROUTINE notification with a RemoteInput inline reply for quick note
 * entry. When the user responds, [PostCallLogReceiver] processes the notes,
 * updates the contact context, and syncs to the desktop brain.
 */
@Singleton
class PostCallLogger @Inject constructor(
    @ApplicationContext private val context: Context,
    private val contactContextDao: ContactContextDao,
    private val callLogDao: CallLogDao,
    private val apiClient: JarvisApiClient,
) {

    private val notificationManager: NotificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Called after a call ends. Prompts the user to log conversation context
     * via an inline reply notification.
     *
     * Only prompts for calls lasting >= 30 seconds (skips very short/missed calls).
     */
    suspend fun promptForContext(
        phoneNumber: String,
        durationSeconds: Int,
        direction: String,
    ) {
        // Check if feature is enabled
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_POST_CALL_LOGGING, true)) return

        // Skip very short calls
        if (durationSeconds < 30) return

        if (!hasNotificationPermission()) return

        val normalizedNumber = normalizeNumber(phoneNumber)

        if (normalizedNumber.isBlank()) return

        // Get or create contact context
        val contact = contactContextDao.getByPhoneNumber(normalizedNumber)
            ?: ContactContextEntity(
                phoneNumber = normalizedNumber,
                contactName = normalizedNumber,
            ).also { contactContextDao.upsert(it) }

        val contactForLog = contactContextDao.getByPhoneNumber(normalizedNumber) ?: return

        // Insert a call log entry (notes will be updated when user responds)
        val todayDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        val callLogId = callLogDao.insert(
            CallLogEntity(
                contactContextId = contactForLog.id,
                phoneNumber = normalizedNumber,
                contactName = contactForLog.contactName,
                direction = direction,
                durationSeconds = durationSeconds,
                timestamp = System.currentTimeMillis(),
                date = todayDate,
            ),
        )

        // Post notification with inline reply
        val durationMin = durationSeconds / 60
        postPromptNotification(
            contactForLog,
            normalizedNumber,
            durationMin,
            direction,
            callLogId,
        )
    }

    private fun postPromptNotification(
        contact: ContactContextEntity,
        normalizedNumber: String,
        durationMin: Int,
        direction: String,
        callLogId: Long,
    ) {
        val notifId = (normalizedNumber.hashCode() and 0x7FFFFFFF) + NOTIFICATION_ID_OFFSET

        // RemoteInput for inline reply
        val remoteInput = RemoteInput.Builder(REMOTE_INPUT_KEY)
            .setLabel("What did you discuss?")
            .build()

        // Log action with RemoteInput
        val logIntent = Intent(context, PostCallLogReceiver::class.java).apply {
            action = ACTION_POST_CALL_LOG
            putExtra(EXTRA_CALL_LOG_ID, callLogId)
            putExtra(EXTRA_CONTACT_CONTEXT_ID, contact.id)
            putExtra(EXTRA_PHONE_NUMBER, normalizedNumber)
            putExtra(EXTRA_NOTIFICATION_ID, notifId)
        }
        val logPending = PendingIntent.getBroadcast(
            context,
            notifId,
            logIntent,
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val logAction = NotificationCompat.Action.Builder(
            android.R.drawable.ic_menu_edit,
            "Quick Log",
            logPending,
        )
            .addRemoteInput(remoteInput)
            .build()

        // Skip action
        val skipIntent = Intent(context, PostCallLogReceiver::class.java).apply {
            action = ACTION_POST_CALL_SKIP
            putExtra(EXTRA_CALL_LOG_ID, callLogId)
            putExtra(EXTRA_CONTACT_CONTEXT_ID, contact.id)
            putExtra(EXTRA_NOTIFICATION_ID, notifId)
        }
        val skipPending = PendingIntent.getBroadcast(
            context,
            notifId + 1,
            skipIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val notification = NotificationCompat.Builder(
            context,
            NotificationPriority.ROUTINE.channelId,
        )
            .setContentTitle("Log your call with ${contact.contactName}")
            .setContentText(
                "You just had a $durationMin min $direction call. " +
                    "Tap to add notes about what you discussed.",
            )
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setAutoCancel(true)
            .setTimeoutAfter(30 * 60 * 1000L) // 30 minutes
            .addAction(logAction)
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel,
                "Skip",
                skipPending,
            )
            .build()

        notificationManager.notify(notifId, notification)
        Log.i(TAG, "Post-call prompt posted for ${contact.contactName}")
    }

    /** Normalize phone number (same logic as PreCallCardManager). */
    private fun normalizeNumber(number: String): String {
        val digitsOnly = number.filter { it.isDigit() }
        return if (digitsOnly.length >= 10) digitsOnly.takeLast(10) else digitsOnly
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
        private const val TAG = "PostCallLogger"
        const val NOTIFICATION_ID_OFFSET = 40000
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_POST_CALL_LOGGING = "post_call_logging_enabled"
        const val REMOTE_INPUT_KEY = "call_notes"
        const val ACTION_POST_CALL_LOG = "com.jarvis.assistant.POST_CALL_LOG"
        const val ACTION_POST_CALL_SKIP = "com.jarvis.assistant.POST_CALL_SKIP"
        const val EXTRA_CALL_LOG_ID = "call_log_id"
        const val EXTRA_CONTACT_CONTEXT_ID = "contact_context_id"
        const val EXTRA_PHONE_NUMBER = "phone_number"
        const val EXTRA_NOTIFICATION_ID = "notification_id"
    }
}

/**
 * BroadcastReceiver that handles post-call logging notification actions.
 *
 * Processes inline reply notes from RemoteInput, updates the CallLogEntity
 * and ContactContextEntity, and syncs conversation context to the desktop brain.
 */
class PostCallLogReceiver : BroadcastReceiver() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface PostCallLogEntryPoint {
        fun contactContextDao(): ContactContextDao
        fun callLogDao(): CallLogDao
        fun apiClient(): JarvisApiClient
    }

    override fun onReceive(context: Context, intent: Intent) {
        val callLogId = intent.getLongExtra(PostCallLogger.EXTRA_CALL_LOG_ID, -1)
        val contactContextId = intent.getLongExtra(PostCallLogger.EXTRA_CONTACT_CONTEXT_ID, -1)
        val notifId = intent.getIntExtra(PostCallLogger.EXTRA_NOTIFICATION_ID, -1)

        if (callLogId < 0 || contactContextId < 0) return

        // Dismiss the notification
        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (notifId >= 0) {
            notificationManager.cancel(notifId)
        }

        val isSkip = intent.action == PostCallLogger.ACTION_POST_CALL_SKIP

        val pendingResult = goAsync()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        scope.launch {
            try {
                val entryPoint = EntryPointAccessors.fromApplication(
                    context.applicationContext,
                    PostCallLogEntryPoint::class.java,
                )
                val callLogDao = entryPoint.callLogDao()
                val contactContextDao = entryPoint.contactContextDao()
                val apiClient = entryPoint.apiClient()

                val callLog = callLogDao.getById(callLogId) ?: return@launch
                val contactContext = contactContextDao.getByPhoneNumber(
                    callLog.phoneNumber,
                ) ?: return@launch

                if (isSkip) {
                    // Mark as skipped
                    callLogDao.update(callLog.copy(notes = "(skipped)"))
                } else {
                    // Extract notes from RemoteInput
                    val results = RemoteInput.getResultsFromIntent(intent)
                    val notes = results?.getCharSequence(PostCallLogger.REMOTE_INPUT_KEY)
                        ?.toString() ?: ""

                    if (notes.isNotBlank()) {
                        // Extract simple topic keywords from notes
                        val newTopics = extractTopics(notes)
                        val topicsJson = mergeTopics(contactContext.keyTopics, newTopics)

                        // Update call log
                        callLogDao.update(
                            callLog.copy(
                                notes = notes,
                                topics = Gson().toJson(newTopics),
                            ),
                        )

                        // Update contact context
                        val todayDate = SimpleDateFormat(
                            "yyyy-MM-dd",
                            Locale.US,
                        ).format(Date())
                        val updatedContact = contactContext.copy(
                            lastNotes = notes,
                            keyTopics = topicsJson,
                            lastCallDate = todayDate,
                            lastCallTimestamp = System.currentTimeMillis(),
                            totalCalls = contactContext.totalCalls + 1,
                            importance = calculateImportance(
                                contactContext.totalCalls + 1,
                                contactContext.createdAt,
                            ),
                            syncedToDesktop = false,
                            updatedAt = System.currentTimeMillis(),
                        )
                        contactContextDao.update(updatedContact)

                        // Sync to desktop brain (best-effort)
                        try {
                            apiClient.api().sendCommand(
                                CommandRequest(
                                    text = "Remember: I spoke with ${contactContext.contactName}" +
                                        " about $notes",
                                ),
                            )
                        } catch (_: Exception) {
                            // Desktop sync is best-effort
                        }

                        Log.i(TAG, "Post-call notes saved for ${contactContext.contactName}")
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error processing post-call log", e)
            } finally {
                pendingResult.finish()
            }
        }
    }

    /**
     * Extract simple topic keywords from notes.
     * Splits by commas or periods, takes first 5 non-trivial phrases.
     */
    private fun extractTopics(notes: String): List<String> {
        return notes.split(Regex("[,.]"))
            .map { it.trim() }
            .filter { it.length >= 3 && it.length <= 50 }
            .take(5)
    }

    /**
     * Merge new topics into existing topics JSON, keeping the last 10 unique topics.
     */
    private fun mergeTopics(existingJson: String, newTopics: List<String>): String {
        val existing = try {
            val type = object : TypeToken<List<String>>() {}.type
            Gson().fromJson<List<String>>(existingJson, type) ?: emptyList()
        } catch (_: Exception) {
            emptyList()
        }

        val merged = (existing + newTopics)
            .distinct()
            .takeLast(10)

        return Gson().toJson(merged)
    }

    /**
     * Calculate importance based on call frequency and recency.
     */
    private fun calculateImportance(totalCalls: Int, createdAt: Long): Float {
        val monthsSinceCreated = maxOf(
            1.0f,
            (System.currentTimeMillis() - createdAt) / (30L * 24 * 60 * 60 * 1000).toFloat(),
        )
        val callFrequency = (totalCalls / monthsSinceCreated).coerceAtMost(1.0f)
        // recency is 1.0 since this is being called right after a call
        val recency = 1.0f
        return (callFrequency * 0.4f + recency * 0.6f).coerceIn(0.0f, 1.0f)
    }

    companion object {
        private const val TAG = "PostCallLogReceiver"
    }
}
