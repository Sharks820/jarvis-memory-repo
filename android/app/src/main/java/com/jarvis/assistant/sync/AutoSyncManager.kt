package com.jarvis.assistant.sync

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.data.CommandQueueProcessor
import com.jarvis.assistant.intelligence.IntelligenceMerger
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Manages automatic synchronization between phone and desktop.
 *
 * Key capabilities:
 * - **Connectivity monitoring**: Detects network state changes and triggers sync
 * - **Dynamic URL switching**: Tries LAN URL first (fast), falls back to relay URL
 * - **Reconnection sync**: Immediately syncs when network comes back
 * - **Adaptive intervals**: Adjusts sync frequency based on connectivity and app state
 * - **Heartbeat**: Periodic lightweight check to confirm desktop reachability
 *
 * This replaces the old "same WiFi only" model. The phone can now reach the
 * desktop from anywhere via the relay URL (Cloudflare Tunnel, Tailscale, etc).
 */
@Singleton
class AutoSyncManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiClient: JarvisApiClient,
    private val processor: CommandQueueProcessor,
    private val intelligenceMerger: IntelligenceMerger,
    private val syncConfig: SyncConfigStore,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var syncLoopJob: Job? = null
    private var heartbeatJob: Job? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    @Volatile var isDesktopReachable = false
        private set

    @Volatile var lastSyncTime = 0L
        private set

    @Volatile private var consecutiveFailures = 0

    /**
     * Start monitoring connectivity and syncing automatically.
     * Call this from JarvisService.onCreate().
     */
    fun start() {
        registerNetworkCallback()
        startSyncLoop()
        startHeartbeatLoop()
        // Fetch sync config from desktop on startup
        scope.launch { refreshSyncConfig() }
        Log.i(TAG, "AutoSyncManager started")
    }

    /**
     * Stop all sync operations. Call this from JarvisService.onDestroy().
     */
    fun stop() {
        syncLoopJob?.cancel()
        heartbeatJob?.cancel()
        unregisterNetworkCallback()
        Log.i(TAG, "AutoSyncManager stopped")
    }

    /**
     * Register a network callback to detect connectivity changes.
     * When network comes back, immediately trigger a full sync.
     */
    private fun registerNetworkCallback() {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return

        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                Log.i(TAG, "Network available — triggering reconnection sync")
                if (syncConfig.syncOnReconnect) {
                    scope.launch { performFullSync() }
                }
            }

            override fun onLost(network: Network) {
                Log.i(TAG, "Network lost — switching to offline mode")
                isDesktopReachable = false
            }

            override fun onCapabilitiesChanged(
                network: Network,
                capabilities: NetworkCapabilities,
            ) {
                // Detect if we're on WiFi (likely same LAN as desktop) or cellular
                val onWifi = capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                syncConfig.isOnWifi = onWifi
            }
        }

        try {
            cm.registerNetworkCallback(request, callback)
            networkCallback = callback
        } catch (e: Exception) {
            Log.w(TAG, "Failed to register network callback: ${e.message}")
        }
    }

    private fun unregisterNetworkCallback() {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        val cb = networkCallback
        if (cm != null && cb != null) {
            try {
                cm.unregisterNetworkCallback(cb)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to unregister network callback: ${e.message}")
            }
        }
        networkCallback = null
    }

    /**
     * Main sync loop with adaptive intervals.
     * Syncs more frequently when desktop is reachable, backs off when not.
     */
    private fun startSyncLoop() {
        syncLoopJob?.cancel()
        syncLoopJob = scope.launch {
            while (true) {
                try {
                    processor.flushPending()
                } catch (e: Exception) {
                    Log.w(TAG, "Sync loop flush error: ${e.message}")
                }

                val interval = if (isDesktopReachable) {
                    syncConfig.syncIntervalConnected
                } else {
                    syncConfig.syncIntervalDisconnected
                }
                delay(interval * 1000L)
            }
        }
    }

    /**
     * Periodic heartbeat to check if desktop is reachable.
     * Uses the lightweight /sync/heartbeat endpoint.
     */
    private fun startHeartbeatLoop() {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            while (true) {
                try {
                    val response = apiClient.api().health()
                    isDesktopReachable = response.status == "ok"
                    if (isDesktopReachable) {
                        consecutiveFailures = 0
                    }
                } catch (e: Exception) {
                    isDesktopReachable = false
                    consecutiveFailures++
                }

                // Heartbeat interval: 30s when connected, exponential backoff when not
                val interval = if (isDesktopReachable) {
                    30_000L
                } else {
                    val backoff = syncConfig.retryBackoffBase * (1L shl minOf(consecutiveFailures, 6))
                    minOf(backoff * 1000L, syncConfig.retryBackoffMax * 1000L)
                }
                delay(interval)
            }
        }
    }

    /**
     * Perform a full bidirectional sync: pull changes from desktop,
     * push local changes to desktop.
     */
    private suspend fun performFullSync() {
        try {
            // First, flush any pending commands
            processor.flushPending()

            // Then check reachability
            val response = apiClient.api().health()
            isDesktopReachable = response.status == "ok"

            if (isDesktopReachable) {
                consecutiveFailures = 0
                lastSyncTime = System.currentTimeMillis()

                // Bidirectional intelligence merge — this is what makes
                // both brains smarter together. Phone pushes local learnings
                // (context, habits, interactions), desktop pushes knowledge
                // graph facts and analysis results.
                try {
                    val mergeResult = intelligenceMerger.fullMerge()
                    Log.i(TAG, "Intelligence merge: pushed=${mergeResult.pushed}, " +
                        "pulled=${mergeResult.pulled}")
                } catch (e: Exception) {
                    Log.w(TAG, "Intelligence merge failed: ${e.message}")
                }

                Log.i(TAG, "Full sync completed successfully")
            }
        } catch (e: Exception) {
            isDesktopReachable = false
            consecutiveFailures++
            Log.w(TAG, "Full sync failed: ${e.message}")
        }
    }

    /**
     * Fetch the latest sync configuration from the desktop.
     * Updates intervals, relay URL, cache settings, etc.
     */
    private suspend fun refreshSyncConfig() {
        try {
            val response = apiClient.api().getSyncConfig()
            if (response.ok) {
                syncConfig.applyRemoteConfig(response.config)
                // Update the API client's URL strategy based on new config
                apiClient.updateRelayUrl(syncConfig.relayUrl)
                Log.i(TAG, "Sync config refreshed from desktop")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to refresh sync config: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "AutoSync"
    }
}
