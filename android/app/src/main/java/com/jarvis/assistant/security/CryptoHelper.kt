package com.jarvis.assistant.security

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Encrypted storage for API credentials and sensitive configuration.
 *
 * Uses AndroidX EncryptedSharedPreferences backed by the Android Keystore.
 */
class CryptoHelper(context: Context) {

    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "jarvis_secure_prefs",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun getBaseUrl(): String = prefs.getString(KEY_BASE_URL, "") ?: ""
    fun setBaseUrl(url: String) = prefs.edit().putString(KEY_BASE_URL, url).apply()

    fun getToken(): String = prefs.getString(KEY_TOKEN, "") ?: ""
    fun setToken(token: String) = prefs.edit().putString(KEY_TOKEN, token).apply()

    fun getSigningKey(): String = prefs.getString(KEY_SIGNING_KEY, "") ?: ""
    fun setSigningKey(key: String) = prefs.edit().putString(KEY_SIGNING_KEY, key).apply()

    fun getDeviceId(): String = prefs.getString(KEY_DEVICE_ID, "") ?: ""
    fun setDeviceId(id: String) = prefs.edit().putString(KEY_DEVICE_ID, id).apply()

    fun getMasterPassword(): String = prefs.getString(KEY_MASTER_PASSWORD, "") ?: ""
    fun setMasterPassword(password: String) = prefs.edit().putString(KEY_MASTER_PASSWORD, password).apply()

    fun isBootstrapped(): Boolean =
        getToken().isNotBlank() && getSigningKey().isNotBlank()

    fun clearAll() = prefs.edit().clear().apply()

    /**
     * Returns a stable fallback passphrase for SQLCipher, generated once via
     * SecureRandom and persisted in EncryptedSharedPreferences.
     */
    @Synchronized
    fun getOrCreateFallbackPassphrase(): String {
        val existing = prefs.getString(KEY_SQLCIPHER_FALLBACK, null)
        if (!existing.isNullOrBlank()) return existing
        val bytes = ByteArray(32)
        java.security.SecureRandom().nextBytes(bytes)
        val generated = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
        prefs.edit().putString(KEY_SQLCIPHER_FALLBACK, generated).commit()
        return generated
    }

    companion object {
        private const val KEY_BASE_URL = "desktop_base_url"
        private const val KEY_TOKEN = "api_token"
        private const val KEY_SIGNING_KEY = "signing_key"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_MASTER_PASSWORD = "master_password"
        private const val KEY_SQLCIPHER_FALLBACK = "sqlcipher_fallback_passphrase"
    }
}
