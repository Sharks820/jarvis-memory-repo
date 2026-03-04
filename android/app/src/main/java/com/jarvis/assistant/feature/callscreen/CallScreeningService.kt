package com.jarvis.assistant.feature.callscreen

import android.app.Activity
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.telecom.Call
import android.telecom.CallScreeningService
import android.telecom.Connection
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import com.jarvis.assistant.api.JarvisApiClient
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Android [CallScreeningService] that intercepts incoming calls before the
 * phone rings, scores them against the local spam database via [SpamScorer],
 * and applies the appropriate response (block / silence / voicemail / allow).
 *
 * Requires the user to grant [RoleManager.ROLE_CALL_SCREENING] permission.
 *
 * NOTE: CallScreeningService cannot use @AndroidEntryPoint directly because
 * it is not a standard Hilt-supported lifecycle component. We use @EntryPoint
 * with EntryPointAccessors for manual Hilt injection instead.
 */
class JarvisCallScreeningService : CallScreeningService() {

    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface CallScreenEntryPoint {
        fun spamScorer(): SpamScorer
        fun apiClient(): JarvisApiClient
    }

    private val entryPoint by lazy {
        EntryPointAccessors.fromApplication(
            application,
            CallScreenEntryPoint::class.java,
        )
    }

    private val spamScorer by lazy { entryPoint.spamScorer() }
    private val apiClient by lazy { entryPoint.apiClient() }

    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    override fun onScreenCall(callDetails: Call.Details) {
        val number = try {
            callDetails.handle?.schemeSpecificPart ?: ""
        } catch (e: Exception) {
            Log.e(TAG, "Error extracting phone number: ${e.message}")
            respondToCall(callDetails, CallResponse.Builder().build())
            return
        }
        if (number.isBlank()) {
            respondToCall(callDetails, CallResponse.Builder().build())
            return
        }

        // Check if call screening is enabled
        val prefs = getSharedPreferences(SpamScorer.PREFS_NAME, Context.MODE_PRIVATE)
        val enabled = prefs.getBoolean(SpamScorer.KEY_ENABLED, true)
        if (!enabled) {
            respondToCall(callDetails, CallResponse.Builder().build())
            return
        }

        // Extract STIR/SHAKEN verification status (API 30+)
        val stirStatus = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            when (callDetails.callerNumberVerificationStatus) {
                Connection.VERIFICATION_STATUS_PASSED -> "passed"
                Connection.VERIFICATION_STATUS_FAILED -> "failed"
                else -> "not_verified"
            }
        } else {
            "not_verified"
        }

        // Extract presentation type
        val presentation = when (callDetails.handlePresentation) {
            android.telecom.TelecomManager.PRESENTATION_RESTRICTED -> "restricted"
            android.telecom.TelecomManager.PRESENTATION_UNKNOWN -> "unknown"
            android.telecom.TelecomManager.PRESENTATION_PAYPHONE -> "payphone"
            else -> "allowed"
        }

        // Score on IO dispatcher since it hits Room DB
        serviceScope.launch {
            try {
                val normalized = spamScorer.normalizeNumber(number)
                val result = spamScorer.score(normalized)

                // Boost score with STIR/SHAKEN signal locally
                val boostedScore = spamScorer.boostWithStir(result.score, stirStatus, presentation)
                val action = spamScorer.actionForScore(boostedScore)

                Log.d(
                    TAG,
                    "Call from ${maskNumber(normalized)} scored ${result.score} " +
                        "(boosted=$boostedScore) stir=$stirStatus pres=$presentation -> $action",
                )

                val response = when (action) {
                    "block" -> CallResponse.Builder()
                        .setDisallowCall(true)
                        .setRejectCall(true)
                        .setSkipCallLog(false)
                        .setSkipNotification(true)
                        .build()

                    "silence" -> CallResponse.Builder()
                        .setDisallowCall(false)
                        .setSilenceCall(true)
                        .setSkipCallLog(false)
                        .setSkipNotification(false)
                        .build()

                    "voicemail" -> CallResponse.Builder()
                        .setDisallowCall(true)
                        .setRejectCall(false) // sends to voicemail
                        .setSkipCallLog(false)
                        .setSkipNotification(false)
                        .build()

                    else -> CallResponse.Builder().build() // "allow" -- no action
                }

                respondToCall(callDetails, response)

                // Async: report call to desktop for campaign analysis (fire-and-forget)
                reportCallToDesktop(normalized, stirStatus, presentation, action)
            } catch (e: Exception) {
                Log.e(TAG, "Error screening call from ${maskNumber(number)}: ${e.message}")
                // On error, allow the call through
                respondToCall(callDetails, CallResponse.Builder().build())
            }
        }
    }

    /**
     * Report screened call to desktop for scam campaign detection.
     * Fire-and-forget — failure here never affects call screening.
     */
    private fun reportCallToDesktop(
        number: String,
        stirStatus: String,
        presentation: String,
        action: String,
    ) {
        serviceScope.launch {
            try {
                apiClient.api().reportScamCall(
                    mapOf(
                        "number" to number,
                        "stir_status" to stirStatus,
                        "presentation" to presentation,
                        "duration_sec" to 0,
                        "answered" to (action == "allow"),
                        "contact_name" to "",
                    ),
                )
            } catch (e: Exception) {
                Log.d(TAG, "Scam report-call to desktop failed (non-fatal): ${e.message}")
            }
        }
    }

    override fun onDestroy() {
        serviceScope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "JarvisCallScreen"

        /** Mask a phone number to show only the last 4 digits for PII safety. */
        private fun maskNumber(number: String): String {
            val digits = number.filter { it.isDigit() }
            return if (digits.length >= 4) "***" + digits.takeLast(4) else "***"
        }
    }
}

// Utility functions for call screening role management

/**
 * Check whether the app currently holds the call screening role.
 * Returns false on API < 29 (Q) where RoleManager is unavailable.
 */
fun isCallScreeningRoleGranted(context: Context): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false
    val roleManager = context.getSystemService(RoleManager::class.java) ?: return false
    return roleManager.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
}

/**
 * Register an activity result launcher that requests the call screening role.
 * Call this from  of a [ComponentActivity], then invoke the
 * returned launcher when the user taps the permission button.
 *
 * @param activity the host activity
 * @param onResult callback with  if role was granted
 * @return the launcher, or  if the role is not available
 */
fun registerCallScreeningRoleLauncher(
    activity: ComponentActivity,
    onResult: (Boolean) -> Unit,
): ActivityResultLauncher<Intent>? {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
    val roleManager = activity.getSystemService(RoleManager::class.java) ?: return null
    if (!roleManager.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING)) {
        return null
    }

    return activity.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        onResult(result.resultCode == Activity.RESULT_OK)
    }
}

/**
 * Create the intent to request the call screening role.
 */
/**
 * Create the intent to request the call screening role.
 * Returns null on API < 29 (Q) where RoleManager is unavailable.
 */
fun createCallScreeningRoleIntent(context: Context): Intent? {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
    val roleManager = context.getSystemService(RoleManager::class.java) ?: return null
    return roleManager.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)
}
