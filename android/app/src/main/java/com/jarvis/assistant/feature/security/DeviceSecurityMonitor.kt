package com.jarvis.assistant.feature.security

import android.app.admin.DevicePolicyManager
import android.app.KeyguardManager
import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.provider.Settings
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Comprehensive device security health checker.
 *
 * Runs periodic checks for root, ADB, unknown sources, dangerous permissions,
 * lock screen, and encryption status. Results are reported as a [SecurityReport]
 * with severity level and detailed findings.
 */
@Singleton
class DeviceSecurityMonitor @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    /**
     * Run all security checks and return an aggregate report.
     */
    fun runFullScan(): SecurityReport {
        val findings = mutableListOf<SecurityFinding>()

        findings.addAll(checkRootStatus())
        findings.addAll(checkDeveloperOptions())
        findings.addAll(checkAdbStatus())
        findings.addAll(checkUnknownSources())
        findings.addAll(checkLockScreen())
        findings.addAll(checkEncryption())
        findings.addAll(checkDangerousPermissions())

        val severity = when {
            findings.any { it.severity == Severity.CRITICAL } -> Severity.CRITICAL
            findings.any { it.severity == Severity.WARNING } -> Severity.WARNING
            else -> Severity.SAFE
        }

        return SecurityReport(
            severity = severity,
            findings = findings,
            timestamp = System.currentTimeMillis(),
        )
    }

    // ── Root Detection ──────────────────────────────────────────────

    internal fun checkRootStatus(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()

        // Check for su binary in common locations
        val suPaths = listOf(
            "/system/bin/su", "/system/xbin/su", "/sbin/su",
            "/data/local/xbin/su", "/data/local/bin/su",
            "/system/sd/xbin/su", "/system/bin/failsafe/su",
            "/data/local/su", "/su/bin/su",
        )
        if (suPaths.any { File(it).exists() }) {
            findings.add(
                SecurityFinding(
                    category = "Root Access",
                    description = "su binary found on device",
                    severity = Severity.CRITICAL,
                    recommendation = "Device appears rooted. This bypasses Android security sandbox.",
                ),
            )
        }

        // Check for Magisk
        val magiskPackages = listOf(
            "com.topjohnwu.magisk",
            "io.github.vvb2060.magisk",
            "de.robv.android.xposed",
        )
        val installedRootPkgs = magiskPackages.filter { isPackageInstalled(it) }
        if (installedRootPkgs.isNotEmpty()) {
            findings.add(
                SecurityFinding(
                    category = "Root Access",
                    description = "Root management apps detected: ${installedRootPkgs.joinToString()}",
                    severity = Severity.CRITICAL,
                    recommendation = "Remove root management software for maximum security.",
                ),
            )
        }

        // Check for common root-indicator packages
        val rootIndicators = listOf(
            "com.noshufou.android.su",
            "com.thirdparty.superuser",
            "eu.chainfire.supersu",
            "com.koushikdutta.superuser",
        )
        val foundIndicators = rootIndicators.filter { isPackageInstalled(it) }
        if (foundIndicators.isNotEmpty()) {
            findings.add(
                SecurityFinding(
                    category = "Root Access",
                    description = "Superuser apps detected: ${foundIndicators.joinToString()}",
                    severity = Severity.CRITICAL,
                    recommendation = "Superuser apps indicate a rooted or previously rooted device.",
                ),
            )
        }

        return findings
    }

    // ── Developer Options ───────────────────────────────────────────

    internal fun checkDeveloperOptions(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()
        try {
            val devEnabled = Settings.Global.getInt(
                context.contentResolver,
                Settings.Global.DEVELOPMENT_SETTINGS_ENABLED,
                0,
            )
            if (devEnabled == 1) {
                findings.add(
                    SecurityFinding(
                        category = "Developer Options",
                        description = "Developer options are enabled",
                        severity = Severity.WARNING,
                        recommendation = "Disable developer options when not actively debugging.",
                    ),
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check developer options: ${e.message}")
        }
        return findings
    }

    // ── ADB Debugging ───────────────────────────────────────────────

    internal fun checkAdbStatus(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()
        try {
            val adbEnabled = Settings.Global.getInt(
                context.contentResolver,
                Settings.Global.ADB_ENABLED,
                0,
            )
            if (adbEnabled == 1) {
                findings.add(
                    SecurityFinding(
                        category = "USB Debugging",
                        description = "ADB (USB debugging) is enabled",
                        severity = Severity.WARNING,
                        recommendation = "Disable USB debugging to prevent unauthorized access via ADB.",
                    ),
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check ADB status: ${e.message}")
        }
        return findings
    }

    // ── Unknown Sources ─────────────────────────────────────────────

    @Suppress("DEPRECATION")
    internal fun checkUnknownSources(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()
        try {
            // On API 26+ the global setting is deprecated — per-app install permission
            // is used instead. Check both: legacy setting and per-app canRequestPackageInstalls.
            val unknownSources = Settings.Secure.getInt(
                context.contentResolver,
                Settings.Secure.INSTALL_NON_MARKET_APPS,
                0,
            )
            if (unknownSources == 1) {
                findings.add(
                    SecurityFinding(
                        category = "Unknown Sources",
                        description = "Installation from unknown sources is enabled (legacy setting)",
                        severity = Severity.WARNING,
                        recommendation = "Disable unknown sources to prevent sideloaded malware.",
                    ),
                )
            }

            // API 26+: check if any non-system apps have per-app install permission
            val pm = context.packageManager
            @Suppress("DEPRECATION")
            val packages = pm.getInstalledPackages(PackageManager.GET_PERMISSIONS)
            val sideloadApps = packages.filter { pkg ->
                val isSystem = (pkg.applicationInfo?.flags ?: 0) and
                    android.content.pm.ApplicationInfo.FLAG_SYSTEM != 0
                !isSystem && pkg.requestedPermissions?.contains(
                    "android.permission.REQUEST_INSTALL_PACKAGES",
                ) == true
            }.map { it.packageName }
            if (sideloadApps.isNotEmpty()) {
                findings.add(
                    SecurityFinding(
                        category = "Unknown Sources",
                        description = "${sideloadApps.size} app(s) request install-packages permission: ${sideloadApps.take(3).joinToString()}",
                        severity = Severity.WARNING,
                        recommendation = "Review which apps have install permission in Settings > Apps > Special access > Install unknown apps.",
                    ),
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check unknown sources: ${e.message}")
        }
        return findings
    }

    // ── Lock Screen ─────────────────────────────────────────────────

    internal fun checkLockScreen(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()
        try {
            val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
            if (keyguard != null && !keyguard.isDeviceSecure) {
                findings.add(
                    SecurityFinding(
                        category = "Lock Screen",
                        description = "No secure lock screen configured (PIN/pattern/biometric)",
                        severity = Severity.CRITICAL,
                        recommendation = "Set up a PIN, pattern, or biometric lock screen immediately.",
                    ),
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check lock screen: ${e.message}")
        }
        return findings
    }

    // ── Encryption Status ───────────────────────────────────────────

    internal fun checkEncryption(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()
        try {
            val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager
            if (dpm != null) {
                val status = dpm.storageEncryptionStatus
                when (status) {
                    DevicePolicyManager.ENCRYPTION_STATUS_ACTIVE,
                    DevicePolicyManager.ENCRYPTION_STATUS_ACTIVE_PER_USER -> {
                        // Fully encrypted — no finding
                    }
                    DevicePolicyManager.ENCRYPTION_STATUS_ACTIVE_DEFAULT_KEY -> {
                        findings.add(
                            SecurityFinding(
                                category = "Encryption",
                                description = "Device storage is encrypted but using default key (status=$status)",
                                severity = Severity.WARNING,
                                recommendation = "Set a PIN/pattern/password to upgrade from default-key encryption.",
                            ),
                        )
                    }
                    else -> {
                        findings.add(
                            SecurityFinding(
                                category = "Encryption",
                                description = "Device storage is not fully encrypted (status=$status)",
                                severity = Severity.CRITICAL,
                                recommendation = "Enable full-disk or file-based encryption in device settings.",
                            ),
                        )
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check encryption status: ${e.message}")
        }
        return findings
    }

    // ── Dangerous Permission Audit ──────────────────────────────────

    internal fun checkDangerousPermissions(): List<SecurityFinding> {
        val findings = mutableListOf<SecurityFinding>()

        val dangerousPerms = setOf(
            "android.permission.RECORD_AUDIO",
            "android.permission.CAMERA",
            "android.permission.READ_SMS",
            "android.permission.READ_CALL_LOG",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.READ_CONTACTS",
            "android.permission.READ_PHONE_STATE",
        )

        // Well-known system apps that legitimately need many permissions
        val whitelisted = setOf(
            "com.google.android.apps.messaging",
            "com.google.android.dialer",
            "com.samsung.android.messaging",
            "com.samsung.android.dialer",
            "com.samsung.android.incallui",
            "com.google.android.gm",
            "com.android.phone",
            "com.android.mms",
            context.packageName, // Jarvis itself
        )

        try {
            val pm = context.packageManager
            @Suppress("DEPRECATION")
            val packages = pm.getInstalledPackages(PackageManager.GET_PERMISSIONS)
            for (pkg in packages) {
                if (pkg.packageName in whitelisted) continue
                val perms = pkg.requestedPermissions ?: continue
                val permFlags = pkg.requestedPermissionsFlags ?: IntArray(0)
                val grantedDangerous = perms.filterIndexed { index, perm ->
                    perm in dangerousPerms &&
                        index < permFlags.size &&
                        (permFlags[index] and PackageInfo.REQUESTED_PERMISSION_GRANTED) != 0
                }
                if (grantedDangerous.size >= 3) {
                    findings.add(
                        SecurityFinding(
                            category = "Dangerous Permissions",
                            description = "${pkg.packageName} has ${grantedDangerous.size} granted dangerous permissions: " +
                                grantedDangerous.joinToString { it.substringAfterLast(".") },
                            severity = Severity.WARNING,
                            recommendation = "Review whether this app needs all granted permissions.",
                        ),
                    )
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not audit permissions: ${e.message}")
        }
        return findings
    }

    @Suppress("DEPRECATION")
    private fun isPackageInstalled(packageName: String): Boolean {
        return try {
            context.packageManager.getPackageInfo(packageName, 0)
            true
        } catch (_: PackageManager.NameNotFoundException) {
            false
        }
    }

    companion object {
        private const val TAG = "DeviceSecurityMonitor"
        const val PREFS_NAME = "jarvis_security_prefs"
        const val KEY_ENABLED = "security_monitoring_enabled"
        const val KEY_LAST_SCAN = "last_security_scan"
        const val SCAN_INTERVAL_MS = 6L * 60 * 60 * 1000 // 6 hours
    }
}

// ── Data Classes ────────────────────────────────────────────────────

enum class Severity { SAFE, WARNING, CRITICAL }

data class SecurityFinding(
    val category: String,
    val description: String,
    val severity: Severity,
    val recommendation: String,
)

data class SecurityReport(
    val severity: Severity,
    val findings: List<SecurityFinding>,
    val timestamp: Long,
)
