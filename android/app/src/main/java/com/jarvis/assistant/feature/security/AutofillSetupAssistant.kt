package com.jarvis.assistant.feature.security

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.util.Log
import android.view.autofill.AutofillManager
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Helps the user configure Bitdefender SecurePass as the system autofill provider.
 *
 * Checks current autofill status, detects if SecurePass is installed,
 * and provides guidance to enable it as the default autofill service.
 */
@Singleton
class AutofillSetupAssistant @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    /**
     * Get the current autofill configuration status.
     */
    fun getAutofillStatus(): AutofillStatus {
        val afm = context.getSystemService(AutofillManager::class.java)

        val isSupported = afm?.isAutofillSupported ?: false
        // hasEnabledAutofillServices() only checks if the CALLING app is the provider —
        // use Settings.Secure to detect if ANY autofill provider is configured system-wide.
        val hasProvider = try {
            val setting = android.provider.Settings.Secure.getString(
                context.contentResolver, "autofill_service",
            )
            !setting.isNullOrBlank()
        } catch (e: Exception) { false }
        val securePassInstalled = isSecurePassInstalled()

        val state = when {
            !isSupported -> AutofillState.NOT_SUPPORTED
            securePassInstalled && hasProvider && isSecurePassAutofillProvider() ->
                AutofillState.SECUREPASS_ACTIVE
            securePassInstalled && hasProvider -> AutofillState.OTHER_PROVIDER_ACTIVE
            securePassInstalled && !hasProvider -> AutofillState.SECUREPASS_INSTALLED_NOT_CONFIGURED
            !securePassInstalled && hasProvider -> AutofillState.NO_SECUREPASS_OTHER_ACTIVE
            else -> AutofillState.NO_AUTOFILL_CONFIGURED
        }

        return AutofillStatus(
            state = state,
            isAutofillSupported = isSupported,
            hasEnabledProvider = hasProvider,
            isSecurePassInstalled = securePassInstalled,
            currentProviderLabel = getCurrentProviderLabel(),
        )
    }

    /**
     * Check if Bitdefender SecurePass is installed.
     */
    fun isSecurePassInstalled(): Boolean {
        return SECUREPASS_PACKAGES.any { pkg ->
            try {
                context.packageManager.getPackageInfo(pkg, 0)
                true
            } catch (_: PackageManager.NameNotFoundException) {
                false
            }
        }
    }

    /**
     * Heuristic check to see if SecurePass is the current autofill provider.
     * On some Android versions the specific provider can't be queried directly,
     * so we check the enabled autofill services setting.
     */
    private fun isSecurePassAutofillProvider(): Boolean {
        return try {
            val setting = android.provider.Settings.Secure.getString(
                context.contentResolver,
                "autofill_service",
            )
            setting != null && SECUREPASS_PACKAGES.any { setting.contains(it) }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check autofill provider: ${e.message}")
            false
        }
    }

    /**
     * Get a human-readable label for the current autofill provider.
     */
    private fun getCurrentProviderLabel(): String? {
        return try {
            val setting = android.provider.Settings.Secure.getString(
                context.contentResolver,
                "autofill_service",
            )
            when {
                setting.isNullOrBlank() -> null
                SECUREPASS_PACKAGES.any { setting.contains(it) } -> "Bitdefender SecurePass"
                setting.contains("com.google") -> "Google Autofill"
                setting.contains("com.samsung") -> "Samsung Pass"
                setting.contains("com.lastpass") -> "LastPass"
                setting.contains("com.onepassword") || setting.contains("com.agilebits") -> "1Password"
                setting.contains("com.dashlane") -> "Dashlane"
                else -> {
                    // Parse the flattened ComponentName to extract a readable app label
                    val cn = ComponentName.unflattenFromString(setting)
                    if (cn != null) {
                        try {
                            val ai = context.packageManager.getApplicationInfo(cn.packageName, 0)
                            context.packageManager.getApplicationLabel(ai).toString()
                        } catch (_: PackageManager.NameNotFoundException) {
                            cn.packageName
                        }
                    } else {
                        setting.substringAfterLast("/")
                    }
                }
            }
        } catch (e: Exception) {
            null
        }
    }

    companion object {
        private const val TAG = "AutofillSetupAssistant"

        val SECUREPASS_PACKAGES = setOf(
            "com.bitdefender.securepass",
            "com.bitdefender.passwordmanager",
        )

        /**
         * Intent action to open the system autofill settings page.
         * Use with `Settings.ACTION_REQUEST_SET_AUTOFILL_SERVICE`.
         */
        const val ACTION_SET_AUTOFILL = android.provider.Settings.ACTION_REQUEST_SET_AUTOFILL_SERVICE
    }
}

enum class AutofillState {
    /** Device doesn't support autofill (API < 26 or disabled) */
    NOT_SUPPORTED,
    /** SecurePass is installed and active as autofill provider */
    SECUREPASS_ACTIVE,
    /** SecurePass is installed but another provider is active */
    OTHER_PROVIDER_ACTIVE,
    /** SecurePass is installed but no autofill provider configured */
    SECUREPASS_INSTALLED_NOT_CONFIGURED,
    /** SecurePass not installed but another autofill provider is active */
    NO_SECUREPASS_OTHER_ACTIVE,
    /** No autofill provider configured at all */
    NO_AUTOFILL_CONFIGURED,
}

data class AutofillStatus(
    val state: AutofillState,
    val isAutofillSupported: Boolean,
    val hasEnabledProvider: Boolean,
    val isSecurePassInstalled: Boolean,
    val currentProviderLabel: String?,
)
