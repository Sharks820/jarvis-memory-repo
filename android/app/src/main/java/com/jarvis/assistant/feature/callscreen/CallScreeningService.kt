package com.jarvis.assistant.feature.callscreen

import android.app.Activity
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.telecom.Call
import android.telecom.CallScreeningService
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Android [CallScreeningService] that intercepts incoming calls before the
 * phone rings, scores them against the local spam database via [SpamScorer],
 * and applies the appropriate response (block / silence / voicemail / allow).
 *
 * Requires the user to grant [RoleManager.ROLE_CALL_SCREENING] permission.
 */
@AndroidEntryPoint
class JarvisCallScreeningService : CallScreeningService() {

    @Inject lateinit var spamScorer: SpamScorer

    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    override fun onScreenCall(callDetails: Call.Details) {
        val number = callDetails.handle?.schemeSpecificPart ?: ""
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

        // Score on IO dispatcher since it hits Room DB
        serviceScope.launch {
            try {
                val normalized = spamScorer.normalizeNumber(number)
                val result = spamScorer.score(normalized)

                Log.d(TAG, "Call from $normalized scored ${result.score} -> ${result.recommendedAction}")

                val response = when (result.recommendedAction) {
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
            } catch (e: Exception) {
                Log.e(TAG, "Error screening call from $number: ${e.message}")
                // On error, allow the call through
                respondToCall(callDetails, CallResponse.Builder().build())
            }
        }
    }

    companion object {
        private const val TAG = "JarvisCallScreen"
    }
}

// ── Utility functions for call screening role management ─────────────

/**
 * Check whether the app currently holds the call screening role.
 */
fun isCallScreeningRoleGranted(context: Context): Boolean {
    val roleManager = context.getSystemService(RoleManager::class.java)
    return roleManager.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
}

/**
 * Register an activity result launcher that requests the call screening role.
 * Call this from `onCreate()` of a [ComponentActivity], then invoke the
 * returned launcher when the user taps the permission button.
 *
 * @param activity the host activity
 * @param onResult callback with `true` if role was granted
 * @return the launcher, or `null` if the role is not available
 */
fun registerCallScreeningRoleLauncher(
    activity: ComponentActivity,
    onResult: (Boolean) -> Unit,
): ActivityResultLauncher<Intent>? {
    val roleManager = activity.getSystemService(RoleManager::class.java)
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
fun createCallScreeningRoleIntent(context: Context): Intent {
    val roleManager = context.getSystemService(RoleManager::class.java)
    return roleManager.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)
}
