package com.jarvis.assistant.feature.social

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import android.util.Log
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * BroadcastReceiver that listens for [TelephonyManager.ACTION_PHONE_STATE_CHANGED]
 * broadcasts to detect incoming and outgoing call state transitions.
 *
 * Triggers [PreCallCardManager] when a call starts (RINGING/OFFHOOK) and
 * [PostCallLogger] when a call ends (IDLE after OFFHOOK).
 *
 * Uses the EntryPointAccessors pattern for Hilt DI (same as DoseAlarmReceiver).
 * Registered in AndroidManifest with PHONE_STATE intent filter.
 */
class CallStateReceiver : BroadcastReceiver() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface CallStateEntryPoint {
        fun preCallCardManager(): PreCallCardManager
        fun postCallLogger(): PostCallLogger
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return

        val stateStr = intent.getStringExtra(TelephonyManager.EXTRA_STATE) ?: return
        val incomingNumber = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)

        val entryPoint = try {
            EntryPointAccessors.fromApplication(
                context.applicationContext,
                CallStateEntryPoint::class.java,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to get entry point: ${e.message}")
            return
        }

        val preCallCardManager = entryPoint.preCallCardManager()
        val postCallLogger = entryPoint.postCallLogger()

        when (stateStr) {
            TelephonyManager.EXTRA_STATE_RINGING -> {
                // Incoming call ringing
                if (!incomingNumber.isNullOrBlank()) {
                    currentNumber = incomingNumber
                }
                wasIncoming = true
                Log.i(TAG, "RINGING: $currentNumber")

                if (currentNumber.isNotBlank()) {
                    CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
                        try {
                            preCallCardManager.showPreCallCard(currentNumber)
                        } catch (e: Exception) {
                            Log.w(TAG, "Pre-call card error: ${e.message}")
                        }
                    }
                }
            }

            TelephonyManager.EXTRA_STATE_OFFHOOK -> {
                // Call answered or outgoing call started
                callStartTime = System.currentTimeMillis()

                if (lastState == TelephonyManager.EXTRA_STATE_IDLE) {
                    // Outgoing call -- try to get number
                    wasIncoming = false
                    if (!incomingNumber.isNullOrBlank()) {
                        currentNumber = incomingNumber
                    }

                    if (currentNumber.isNotBlank()) {
                        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
                            try {
                                preCallCardManager.showPreCallCard(currentNumber)
                            } catch (e: Exception) {
                                Log.w(TAG, "Pre-call card error: ${e.message}")
                            }
                        }
                    }
                }
                Log.i(TAG, "OFFHOOK: $currentNumber")
            }

            TelephonyManager.EXTRA_STATE_IDLE -> {
                // Call ended
                if (lastState == TelephonyManager.EXTRA_STATE_OFFHOOK && currentNumber.isNotBlank()) {
                    val durationSec = if (callStartTime > 0) {
                        ((System.currentTimeMillis() - callStartTime) / 1000).toInt()
                    } else {
                        0
                    }
                    val direction = if (wasIncoming) "incoming" else "outgoing"

                    Log.i(
                        TAG,
                        "IDLE: Call ended. Duration: ${durationSec}s, " +
                            "Direction: $direction, Number: $currentNumber",
                    )

                    val numberToLog = currentNumber
                    CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
                        try {
                            postCallLogger.promptForContext(
                                numberToLog,
                                durationSec,
                                direction,
                            )
                        } catch (e: Exception) {
                            Log.w(TAG, "Post-call logger error: ${e.message}")
                        }
                    }
                }

                // Reset tracking
                callStartTime = 0L
                currentNumber = ""
                wasIncoming = false
            }
        }

        lastState = stateStr
    }

    companion object {
        private const val TAG = "CallStateReceiver"

        // Track call state transitions across receiver invocations
        @Volatile
        private var lastState = TelephonyManager.EXTRA_STATE_IDLE

        @Volatile
        private var callStartTime = 0L

        @Volatile
        private var currentNumber = ""

        @Volatile
        private var wasIncoming = false
    }
}
