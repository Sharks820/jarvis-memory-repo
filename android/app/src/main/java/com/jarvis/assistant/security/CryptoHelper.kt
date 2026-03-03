package com.jarvis.assistant.security

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.security.MessageDigest

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

    /**
     * Store the SHA-256 hash of the master password (never the plaintext).
     * Also removes any legacy plaintext storage from older versions.
     */
    fun setMasterPassword(password: String) {
        prefs.edit()
            .putString(KEY_MASTER_PASSWORD_HASH, hashPassword(password))
            .remove(KEY_MASTER_PASSWORD)
            .apply()
    }

    /**
     * Verify a candidate password against the stored hash.
     * Automatically migrates legacy plaintext storage on first access.
     */
    fun verifyMasterPassword(password: String): Boolean {
        migrateLegacyPassword()
        val stored = prefs.getString(KEY_MASTER_PASSWORD_HASH, "") ?: ""
        return stored.isNotBlank() && MessageDigest.isEqual(
            stored.toByteArray(Charsets.UTF_8),
            hashPassword(password).toByteArray(Charsets.UTF_8),
        )
    }

    /**
     * Check whether a master password has been configured.
     * Automatically migrates legacy plaintext storage on first access.
     */
    fun hasMasterPassword(): Boolean {
        migrateLegacyPassword()
        return prefs.getString(KEY_MASTER_PASSWORD_HASH, "")?.isNotBlank() == true
    }

    /**
     * One-time migration: if a plaintext password exists from a prior version,
     * hash it and delete the plaintext entry.
     */
    private fun migrateLegacyPassword() {
        val legacy = prefs.getString(KEY_MASTER_PASSWORD, null)
        if (legacy != null && legacy.isNotBlank()) {
            prefs.edit()
                .putString(KEY_MASTER_PASSWORD_HASH, hashPassword(legacy))
                .remove(KEY_MASTER_PASSWORD)
                .apply()
        }
    }

    private fun hashPassword(password: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val hash = digest.digest(password.toByteArray(Charsets.UTF_8))
        return hash.joinToString("") { "%02x".format(it) }
    }

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
        /** @deprecated Legacy plaintext key, kept only for migration detection. */
        private const val KEY_MASTER_PASSWORD = "master_password"
        private const val KEY_MASTER_PASSWORD_HASH = "master_password_hash"
        private const val KEY_SQLCIPHER_FALLBACK = "sqlcipher_fallback_passphrase"
    }
}
