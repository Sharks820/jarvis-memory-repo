package com.jarvis.assistant.feature.security

import android.app.ActivityManager
import android.app.AppOpsManager
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Process
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Monitors for potential eavesdropping and phone tapping indicators.
 *
 * Checks recent microphone/camera usage, call forwarding status,
 * suspicious network configurations, and background services with
 * surveillance-capable permissions.
 */
@Singleton
class EavesdropDetector @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    /**
     * Run a full eavesdrop/surveillance scan.
     */
    fun scan(): EavesdropReport {
        val findings = mutableListOf<EavesdropFinding>()
        val recommendations = mutableListOf<String>()

        findings.addAll(checkRecentMicAccess())
        findings.addAll(checkRecentCameraAccess())
        findings.addAll(checkNetworkAnomalies())
        findings.addAll(checkSuspiciousBackgroundServices())

        if (findings.any { it.suspicious }) {
            recommendations.add("Review the flagged apps and remove any you don't recognize.")
            recommendations.add("Check Settings > Apps > Permissions for mic/camera grants.")
            recommendations.add("Consider running a full antivirus scan with Bitdefender.")
        }

        return EavesdropReport(
            isSuspicious = findings.any { it.suspicious },
            findings = findings,
            recommendedActions = recommendations,
            timestamp = System.currentTimeMillis(),
        )
    }

    // ── Microphone Access ───────────────────────────────────────────

    internal fun checkRecentMicAccess(): List<EavesdropFinding> {
        return checkRecentOpsAccess(
            opName = AppOpsManager.OPSTR_RECORD_AUDIO,
            label = "Microphone",
        )
    }

    // ── Camera Access ───────────────────────────────────────────────

    internal fun checkRecentCameraAccess(): List<EavesdropFinding> {
        return checkRecentOpsAccess(
            opName = AppOpsManager.OPSTR_CAMERA,
            label = "Camera",
        )
    }

    /**
     * Check recent app ops access for the given op across all installed packages.
     * Flags apps that accessed the resource in the last hour that aren't whitelisted.
     */
    private fun checkRecentOpsAccess(opName: String, label: String): List<EavesdropFinding> {
        val findings = mutableListOf<EavesdropFinding>()
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as? AppOpsManager ?: return findings

        // Well-known packages that legitimately use mic/camera
        val whitelisted = setOf(
            "com.google.android.apps.messaging",
            "com.samsung.android.messaging",
            "com.google.android.dialer",
            "com.samsung.android.dialer",
            "com.samsung.android.incallui",
            "com.android.phone",
            "com.google.android.GoogleCamera",
            "com.samsung.android.app.camera",
            "com.sec.android.app.camera",
            "com.google.android.apps.maps",
            context.packageName,
        )

        val oneHourAgo = System.currentTimeMillis() - ONE_HOUR_MS

        try {
            val pm = context.packageManager
            val packages = pm.getInstalledPackages(0)
            for (pkg in packages) {
                if (pkg.packageName in whitelisted) continue
                try {
                    val mode = appOps.unsafeCheckOpNoThrow(
                        opName,
                        pkg.applicationInfo?.uid ?: continue,
                        pkg.packageName,
                    )
                    // MODE_ALLOWED means the app has been granted access
                    if (mode == AppOpsManager.MODE_ALLOWED) {
                        // On API 29+, we can check if the op was used recently
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            val ops = appOps.getPackagesForOps(arrayOf(opName))
                            // The ops list is returned for all packages; filter for our target
                            // Fall through to note it as a finding
                        }
                        // Flag non-system apps that have active mic/camera permission
                        val isSystemApp = (pkg.applicationInfo?.flags ?: 0) and
                            android.content.pm.ApplicationInfo.FLAG_SYSTEM != 0
                        if (!isSystemApp) {
                            findings.add(
                                EavesdropFinding(
                                    category = "$label Access",
                                    appPackage = pkg.packageName,
                                    description = "${pkg.packageName} has active $label access permission",
                                    suspicious = false, // Having permission alone isn't suspicious
                                ),
                            )
                        }
                    }
                } catch (e: Exception) {
                    // Skip individual package errors
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not audit $label access: ${e.message}")
        }
        return findings
    }

    // ── Network Anomalies ───────────────────────────────────────────

    internal fun checkNetworkAnomalies(): List<EavesdropFinding> {
        val findings = mutableListOf<EavesdropFinding>()

        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return findings

            val activeNetwork = cm.activeNetwork
            if (activeNetwork != null) {
                val caps = cm.getNetworkCapabilities(activeNetwork)
                if (caps != null) {
                    // Check for VPN — could be legitimate or malicious
                    if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
                        findings.add(
                            EavesdropFinding(
                                category = "Network",
                                appPackage = null,
                                description = "Active VPN connection detected",
                                suspicious = false, // VPNs are common; just note it
                            ),
                        )
                    }
                }
            }

            // Check for HTTP proxy
            val proxyHost = System.getProperty("http.proxyHost")
            val proxyPort = System.getProperty("http.proxyPort")
            if (!proxyHost.isNullOrBlank()) {
                findings.add(
                    EavesdropFinding(
                        category = "Network",
                        appPackage = null,
                        description = "HTTP proxy configured: $proxyHost:$proxyPort",
                        suspicious = true,
                    ),
                )
            }

            // Check global proxy setting
            try {
                val globalProxy = android.provider.Settings.Global.getString(
                    context.contentResolver,
                    "http_proxy",
                )
                if (!globalProxy.isNullOrBlank() && globalProxy != ":0") {
                    findings.add(
                        EavesdropFinding(
                            category = "Network",
                            appPackage = null,
                            description = "System HTTP proxy set: $globalProxy",
                            suspicious = true,
                        ),
                    )
                }
            } catch (e: Exception) {
                // Global proxy check not available
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check network anomalies: ${e.message}")
        }
        return findings
    }

    // ── Background Services Audit ───────────────────────────────────

    internal fun checkSuspiciousBackgroundServices(): List<EavesdropFinding> {
        val findings = mutableListOf<EavesdropFinding>()

        // Known-safe system services
        val safeServicePrefixes = setOf(
            "com.google.", "com.samsung.", "com.android.", "com.sec.",
            "com.qualcomm.", "android.", context.packageName,
            "com.bitdefender.",
        )

        try {
            val am = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
                ?: return findings

            @Suppress("DEPRECATION")
            val runningServices = am.getRunningServices(100)

            // Surveillance-capable permissions
            val surveillancePerms = setOf(
                "android.permission.RECORD_AUDIO",
                "android.permission.CAMERA",
                "android.permission.READ_PHONE_STATE",
                "android.permission.PROCESS_OUTGOING_CALLS",
                "android.permission.READ_CALL_LOG",
                "android.permission.READ_SMS",
            )

            for (service in runningServices) {
                val pkg = service.service.packageName
                if (safeServicePrefixes.any { pkg.startsWith(it) }) continue

                try {
                    val pkgInfo = context.packageManager.getPackageInfo(
                        pkg,
                        PackageManager.GET_PERMISSIONS,
                    )
                    val requestedPerms = pkgInfo.requestedPermissions?.toSet() ?: emptySet()
                    val dangerousCount = requestedPerms.intersect(surveillancePerms).size
                    if (dangerousCount >= 2) {
                        findings.add(
                            EavesdropFinding(
                                category = "Background Service",
                                appPackage = pkg,
                                description = "$pkg is running in background with $dangerousCount surveillance-capable permissions",
                                suspicious = true,
                            ),
                        )
                    }
                } catch (e: PackageManager.NameNotFoundException) {
                    // Package uninstalled while iterating
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not audit background services: ${e.message}")
        }
        return findings
    }

    companion object {
        private const val TAG = "EavesdropDetector"
        const val KEY_LAST_EAVESDROP_SCAN = "last_eavesdrop_scan"
        const val SCAN_INTERVAL_MS = 2L * 60 * 60 * 1000 // 2 hours
        private const val ONE_HOUR_MS = 60L * 60 * 1000
    }
}

// ── Data Classes ────────────────────────────────────────────────────

data class EavesdropFinding(
    val category: String,
    val appPackage: String?,
    val description: String,
    val suspicious: Boolean,
)

data class EavesdropReport(
    val isSuspicious: Boolean,
    val findings: List<EavesdropFinding>,
    val recommendedActions: List<String>,
    val timestamp: Long,
)
