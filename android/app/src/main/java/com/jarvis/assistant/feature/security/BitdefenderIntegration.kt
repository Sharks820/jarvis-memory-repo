package com.jarvis.assistant.feature.security

import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Monitors Bitdefender notifications to track scan results, threat detections,
 * and update status. Parses notification text from Bitdefender packages.
 */
@Singleton
class BitdefenderIntegration @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * Process a notification from a Bitdefender package.
     * Parses scan results, threats, and update status from the notification text.
     */
    fun processNotification(packageName: String, title: String, text: String) {
        Log.i(TAG, "Bitdefender notification from $packageName: $title")

        val fullText = "$title $text".lowercase()

        when {
            // Check clean/safe FIRST — "no threats" contains "threat" so order matters
            fullText.contains("no threats") || fullText.contains("device is safe") ||
                fullText.contains("protected") || fullText.contains("scan complete") ||
                fullText.contains("scan finished") -> {
                recordScanResult(clean = true, title = title, text = text)
            }
            fullText.contains("threat") || fullText.contains("malware") ||
                fullText.contains("virus") || fullText.contains("infected") -> {
                recordThreatDetection(title, text)
            }
            fullText.contains("update") || fullText.contains("updated") -> {
                recordUpdateStatus(title, text)
            }
            else -> {
                // General Bitdefender notification — log it
                Log.d(TAG, "Bitdefender notification: $title | $text")
            }
        }
    }

    private fun recordThreatDetection(title: String, text: String) {
        val now = System.currentTimeMillis()
        val threatCount = prefs.getInt(KEY_THREATS_FOUND, 0)
        prefs.edit()
            .putLong(KEY_LAST_SCAN_TIME, now)
            .putString(KEY_LAST_SCAN_RESULT, "THREAT_DETECTED")
            .putInt(KEY_THREATS_FOUND, threatCount + 1)
            .putString(KEY_LAST_THREAT_DETAIL, "$title: $text")
            .apply()
        Log.w(TAG, "Bitdefender THREAT detected: $title | $text")
    }

    private fun recordScanResult(clean: Boolean, title: String, text: String) {
        val now = System.currentTimeMillis()
        val editor = prefs.edit()
            .putLong(KEY_LAST_SCAN_TIME, now)
            .putString(KEY_LAST_SCAN_RESULT, if (clean) "CLEAN" else "THREAT_DETECTED")
            .putString(KEY_LAST_SCAN_DETAIL, "$title: $text")
        if (clean) {
            editor.putInt(KEY_THREATS_FOUND, 0)
        }
        editor.apply()
        Log.i(TAG, "Bitdefender scan result: ${if (clean) "clean" else "threats found"}")
    }

    private fun recordUpdateStatus(title: String, text: String) {
        prefs.edit()
            .putLong(KEY_LAST_UPDATE_TIME, System.currentTimeMillis())
            .putString(KEY_LAST_UPDATE_DETAIL, "$title: $text")
            .apply()
        Log.i(TAG, "Bitdefender update: $title")
    }

    /**
     * Get summary of last Bitdefender scan for display in settings.
     */
    fun getLastScanInfo(): BitdefenderScanInfo {
        val lastScanTime = prefs.getLong(KEY_LAST_SCAN_TIME, 0L)
        val result = prefs.getString(KEY_LAST_SCAN_RESULT, null)
        val threatsFound = prefs.getInt(KEY_THREATS_FOUND, 0)
        val lastThreatDetail = prefs.getString(KEY_LAST_THREAT_DETAIL, null)
        val lastUpdateTime = prefs.getLong(KEY_LAST_UPDATE_TIME, 0L)

        return BitdefenderScanInfo(
            lastScanTime = lastScanTime,
            lastResult = result,
            totalThreatsFound = threatsFound,
            lastThreatDetail = lastThreatDetail,
            lastUpdateTime = lastUpdateTime,
            isInstalled = isBitdefenderInstalled(),
        )
    }

    /**
     * Check if any Bitdefender package is installed on the device.
     */
    @Suppress("DEPRECATION")
    fun isBitdefenderInstalled(): Boolean {
        return BITDEFENDER_PACKAGES.any { pkg ->
            try {
                context.packageManager.getPackageInfo(pkg, 0)
                true
            } catch (_: Exception) {
                false
            }
        }
    }

    /**
     * Check if the given package is a Bitdefender app.
     */
    fun isBitdefenderPackage(packageName: String): Boolean {
        return packageName in BITDEFENDER_PACKAGES
    }

    /**
     * Whether the last scan found a threat (used for notification urgency).
     */
    fun hasActiveThreat(): Boolean {
        return prefs.getString(KEY_LAST_SCAN_RESULT, null) == "THREAT_DETECTED"
    }

    companion object {
        private const val TAG = "BitdefenderIntegration"
        const val PREFS_NAME = "jarvis_bitdefender_prefs"

        val BITDEFENDER_PACKAGES = setOf(
            "com.bitdefender.security",
            "com.bitdefender.agent",
            "com.bitdefender.centralmgmt",
            "com.bitdefender.antivirus",
        )

        private const val KEY_LAST_SCAN_TIME = "bd_last_scan_time"
        private const val KEY_LAST_SCAN_RESULT = "bd_last_scan_result"
        private const val KEY_LAST_SCAN_DETAIL = "bd_last_scan_detail"
        private const val KEY_THREATS_FOUND = "bd_threats_found"
        private const val KEY_LAST_THREAT_DETAIL = "bd_last_threat_detail"
        private const val KEY_LAST_UPDATE_TIME = "bd_last_update_time"
        private const val KEY_LAST_UPDATE_DETAIL = "bd_last_update_detail"
    }
}

data class BitdefenderScanInfo(
    val lastScanTime: Long,
    val lastResult: String?,
    val totalThreatsFound: Int,
    val lastThreatDetail: String?,
    val lastUpdateTime: Long,
    val isInstalled: Boolean,
)
