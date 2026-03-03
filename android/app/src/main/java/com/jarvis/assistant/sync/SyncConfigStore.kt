package com.jarvis.assistant.sync

import android.content.Context
import android.content.SharedPreferences
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Persists sync configuration received from the desktop.
 *
 * The phone fetches this config from `/sync/config` and stores it locally
 * so it knows how to behave even when the desktop is unreachable:
 * - Which relay URL to use for remote access
 * - How often to sync in different states
 * - Conflict resolution strategy
 * - Offline cache settings
 */
@Singleton
class SyncConfigStore @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Relay URL for reaching desktop from any network (Cloudflare Tunnel, Tailscale, etc). */
    var relayUrl: String
        get() = prefs.getString(KEY_RELAY_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_RELAY_URL, value).apply()

    /** LAN URL for reaching desktop on the same network (fast, low latency). */
    var lanUrl: String
        get() = prefs.getString(KEY_LAN_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LAN_URL, value).apply()

    /** Sync interval when desktop is reachable (seconds). */
    var syncIntervalConnected: Long
        get() = prefs.getLong(KEY_SYNC_INTERVAL_CONNECTED, 60L)
        set(value) = prefs.edit().putLong(KEY_SYNC_INTERVAL_CONNECTED, value).apply()

    /** Sync interval when desktop is not reachable (seconds). */
    var syncIntervalDisconnected: Long
        get() = prefs.getLong(KEY_SYNC_INTERVAL_DISCONNECTED, 300L)
        set(value) = prefs.edit().putLong(KEY_SYNC_INTERVAL_DISCONNECTED, value).apply()

    /** Sync interval when app is in background (seconds). */
    var syncIntervalBackground: Long
        get() = prefs.getLong(KEY_SYNC_INTERVAL_BACKGROUND, 900L)
        set(value) = prefs.edit().putLong(KEY_SYNC_INTERVAL_BACKGROUND, value).apply()

    /** Whether to trigger an immediate sync when network reconnects. */
    var syncOnReconnect: Boolean
        get() = prefs.getBoolean(KEY_SYNC_ON_RECONNECT, true)
        set(value) = prefs.edit().putBoolean(KEY_SYNC_ON_RECONNECT, value).apply()

    /** Exponential backoff base for retries (seconds). */
    var retryBackoffBase: Long
        get() = prefs.getLong(KEY_RETRY_BACKOFF_BASE, 30L)
        set(value) = prefs.edit().putLong(KEY_RETRY_BACKOFF_BASE, value).apply()

    /** Maximum retry backoff interval (seconds). */
    var retryBackoffMax: Long
        get() = prefs.getLong(KEY_RETRY_BACKOFF_MAX, 1800L)
        set(value) = prefs.edit().putLong(KEY_RETRY_BACKOFF_MAX, value).apply()

    /** Maximum hours to keep commands in offline queue. */
    var maxOfflineQueueAgeHours: Long
        get() = prefs.getLong(KEY_MAX_OFFLINE_QUEUE_AGE, 168L)
        set(value) = prefs.edit().putLong(KEY_MAX_OFFLINE_QUEUE_AGE, value).apply()

    /** Whether to cache command responses for offline use. */
    var cacheResponses: Boolean
        get() = prefs.getBoolean(KEY_CACHE_RESPONSES, true)
        set(value) = prefs.edit().putBoolean(KEY_CACHE_RESPONSES, value).apply()

    /** Maximum number of cached responses. */
    var cacheMaxEntries: Int
        get() = prefs.getInt(KEY_CACHE_MAX_ENTRIES, 500)
        set(value) = prefs.edit().putInt(KEY_CACHE_MAX_ENTRIES, value).apply()

    /** Cache entry TTL in hours. */
    var cacheTtlHours: Long
        get() = prefs.getLong(KEY_CACHE_TTL_HOURS, 72L)
        set(value) = prefs.edit().putLong(KEY_CACHE_TTL_HOURS, value).apply()

    /** Whether currently on WiFi (updated by network callback). */
    @Volatile var isOnWifi: Boolean = false

    /**
     * Apply configuration received from the desktop's /sync/config endpoint.
     */
    fun applyRemoteConfig(config: Map<String, Any?>) {
        val editor = prefs.edit()
        (config["relay_url"] as? String)?.let { editor.putString(KEY_RELAY_URL, it) }
        (config["lan_url"] as? String)?.let { editor.putString(KEY_LAN_URL, it) }
        (config["sync_interval_connected"] as? Number)?.let { editor.putLong(KEY_SYNC_INTERVAL_CONNECTED, it.toLong()) }
        (config["sync_interval_disconnected"] as? Number)?.let { editor.putLong(KEY_SYNC_INTERVAL_DISCONNECTED, it.toLong()) }
        (config["sync_interval_background"] as? Number)?.let { editor.putLong(KEY_SYNC_INTERVAL_BACKGROUND, it.toLong()) }
        (config["sync_on_reconnect"] as? Boolean)?.let { editor.putBoolean(KEY_SYNC_ON_RECONNECT, it) }
        (config["retry_backoff_base_seconds"] as? Number)?.let { editor.putLong(KEY_RETRY_BACKOFF_BASE, it.toLong()) }
        (config["retry_backoff_max_seconds"] as? Number)?.let { editor.putLong(KEY_RETRY_BACKOFF_MAX, it.toLong()) }
        (config["max_offline_queue_age_hours"] as? Number)?.let { editor.putLong(KEY_MAX_OFFLINE_QUEUE_AGE, it.toLong()) }
        (config["phone_cache_responses"] as? Boolean)?.let { editor.putBoolean(KEY_CACHE_RESPONSES, it) }
        (config["phone_cache_max_entries"] as? Number)?.let { editor.putInt(KEY_CACHE_MAX_ENTRIES, it.toInt()) }
        (config["phone_cache_ttl_hours"] as? Number)?.let { editor.putLong(KEY_CACHE_TTL_HOURS, it.toLong()) }
        editor.apply()
    }

    companion object {
        const val PREFS_NAME = "jarvis_sync_config"
        private const val KEY_RELAY_URL = "relay_url"
        private const val KEY_LAN_URL = "lan_url"
        private const val KEY_SYNC_INTERVAL_CONNECTED = "sync_interval_connected"
        private const val KEY_SYNC_INTERVAL_DISCONNECTED = "sync_interval_disconnected"
        private const val KEY_SYNC_INTERVAL_BACKGROUND = "sync_interval_background"
        private const val KEY_SYNC_ON_RECONNECT = "sync_on_reconnect"
        private const val KEY_RETRY_BACKOFF_BASE = "retry_backoff_base"
        private const val KEY_RETRY_BACKOFF_MAX = "retry_backoff_max"
        private const val KEY_MAX_OFFLINE_QUEUE_AGE = "max_offline_queue_age"
        private const val KEY_CACHE_RESPONSES = "cache_responses"
        private const val KEY_CACHE_MAX_ENTRIES = "cache_max_entries"
        private const val KEY_CACHE_TTL_HOURS = "cache_ttl_hours"
    }
}
