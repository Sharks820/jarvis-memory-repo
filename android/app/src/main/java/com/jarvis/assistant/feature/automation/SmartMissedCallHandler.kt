package com.jarvis.assistant.feature.automation

import android.content.Context
import android.telephony.SmsManager
import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.SmartReplyRequest
import com.jarvis.assistant.feature.context.ContextDetector
import com.jarvis.assistant.feature.context.UserContext
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Handles missed calls by auto-sending a contextual SMS when the user is
 * in a meeting, driving, or sleeping.
 *
 * Flow:
 * 1. CallStateReceiver detects RINGING → IDLE (no OFFHOOK = missed call)
 * 2. Calls [handleMissedCall] with the phone number
 * 3. Checks current context — only fires for MEETING, DRIVING, SLEEPING
 * 4. Asks desktop brain for a smart reply (POST /smart-reply)
 * 5. Sends the SMS via SmsManager
 * 6. Posts ROUTINE notification confirming the auto-reply
 *
 * The user can disable this per-context via SharedPreferences.
 */
@Singleton
class SmartMissedCallHandler @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiClient: JarvisApiClient,
    private val contextDetector: ContextDetector,
) {

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * Handle a missed call. Called from CallStateReceiver when
     * lastState == RINGING && currentState == IDLE (no OFFHOOK in between).
     */
    suspend fun handleMissedCall(phoneNumber: String, contactName: String) {
        if (!prefs.getBoolean(KEY_ENABLED, true)) return
        if (phoneNumber.isBlank()) return

        // Only auto-reply in contexts where the user can't pick up
        val currentContext = contextDetector.detectCurrentContext()
        val contextLabel = when (currentContext.context) {
            UserContext.MEETING -> "meeting"
            UserContext.DRIVING -> "driving"
            UserContext.SLEEPING -> "sleeping"
            else -> return // Don't auto-reply in NORMAL or GAMING
        }

        // Check per-context toggle
        if (!prefs.getBoolean("smart_reply_$contextLabel", true)) return

        val displayName = contactName.ifBlank { phoneNumber.takeLast(4) }
        Log.i(TAG, "Missed call from ***${phoneNumber.takeLast(4)} in $contextLabel — generating smart reply")

        try {
            // Ask desktop brain for a contextual reply
            val request = SmartReplyRequest(
                contactName = displayName,
                phoneNumber = phoneNumber,
                context = contextLabel,
            )
            val response = apiClient.api().getSmartReply(request)

            if (!response.ok || response.reply.isBlank()) {
                Log.w(TAG, "Desktop returned no reply, using fallback")
                sendFallbackReply(phoneNumber, displayName, contextLabel)
                return
            }

            sendSms(phoneNumber, response.reply)
            Log.i(TAG, "Smart reply sent to ***${phoneNumber.takeLast(4)}: ${response.reply.take(50)}...")
        } catch (e: Exception) {
            Log.w(TAG, "Desktop unavailable for smart reply: ${e.message}")
            sendFallbackReply(phoneNumber, displayName, contextLabel)
        }
    }

    private fun sendFallbackReply(phoneNumber: String, name: String, contextLabel: String) {
        val reply = when (contextLabel) {
            "meeting" -> "Hey $name, I'm in a meeting. I'll call you back soon. — Sent by Jarvis"
            "driving" -> "Hey $name, I'm driving right now. I'll call you back when I arrive. — Sent by Jarvis"
            "sleeping" -> "Hey $name, I'm unavailable right now. I'll get back to you in the morning. — Sent by Jarvis"
            else -> "Hey $name, I missed your call. I'll call you back soon. — Sent by Jarvis"
        }
        sendSms(phoneNumber, reply)
    }

    private fun sendSms(phoneNumber: String, message: String) {
        if (!prefs.getBoolean(KEY_ACTUALLY_SEND, false)) {
            // Safety: default is to NOT actually send SMS — user must opt in
            Log.i(TAG, "SMS draft (not sent — enable in settings): $message")
            return
        }
        try {
            val smsManager = context.getSystemService(SmsManager::class.java)
            val parts = smsManager.divideMessage(message)
            smsManager.sendMultipartTextMessage(phoneNumber, null, parts, null, null)
            Log.i(TAG, "SMS sent to ***${phoneNumber.takeLast(4)}")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to send SMS: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "SmartMissedCall"
        const val PREFS_NAME = "jarvis_prefs"
        const val KEY_ENABLED = "smart_reply_enabled"
        const val KEY_ACTUALLY_SEND = "smart_reply_send_sms"
    }
}
