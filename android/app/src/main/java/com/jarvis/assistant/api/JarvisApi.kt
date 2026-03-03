package com.jarvis.assistant.api

import com.jarvis.assistant.api.models.BootstrapResponse
import com.jarvis.assistant.api.models.CertFingerprintResponse
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.api.models.CommandResponse
import com.jarvis.assistant.api.models.DashboardResponse
import com.jarvis.assistant.api.models.HealthResponse
import com.jarvis.assistant.api.models.SettingsResponse
import com.jarvis.assistant.api.models.SpamCandidatesResponse
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST

/** Retrofit interface matching the desktop engine's mobile API (port 8787). */
interface JarvisApi {

    @GET("/health")
    suspend fun health(): HealthResponse

    /** Get TLS certificate SHA-256 fingerprint for TOFU cert pinning. */
    @GET("/cert-fingerprint")
    suspend fun getCertFingerprint(): CertFingerprintResponse

    @POST("/bootstrap")
    suspend fun bootstrap(
        @Header("X-Jarvis-Master-Password") masterPassword: String,
        @Body body: Map<String, String>,
    ): BootstrapResponse

    @POST("/command")
    suspend fun sendCommand(@Body request: CommandRequest): CommandResponse

    @GET("/settings")
    suspend fun getSettings(): SettingsResponse

    @POST("/settings")
    suspend fun setSettings(@Body settings: Map<String, Any>): Map<String, Any>

    @GET("/dashboard")
    suspend fun getDashboard(): DashboardResponse

    /**
     * Fetch spam candidates directly (future desktop endpoint).
     * Currently not implemented on desktop; SpamDatabaseSync falls back
     * to the /command endpoint with "show spam report".
     */
    @GET("/spam/candidates")
    suspend fun getSpamCandidates(): SpamCandidatesResponse

    // ── Auto-sync endpoints ─────────────────────────────────────────────

    /**
     * Get sync configuration from desktop: relay URL, sync intervals,
     * conflict strategy, cache settings. The phone stores this config
     * locally so it knows how to behave even when desktop is unreachable.
     */
    @GET("/sync/config")
    suspend fun getSyncConfig(): SyncConfigResponse

    /**
     * Lightweight heartbeat to confirm desktop reachability.
     * Returns minimal payload for speed — used for connectivity checks.
     */
    @GET("/sync/heartbeat")
    suspend fun syncHeartbeat(): HeartbeatResponse
}

/** Response from /sync/config. */
data class SyncConfigResponse(
    val ok: Boolean = false,
    val config: Map<String, Any?> = emptyMap(),
)

/** Response from /sync/heartbeat. */
data class HeartbeatResponse(
    val ok: Boolean = false,
    val server_time: Long = 0,
    val device_id: String = "",
)
