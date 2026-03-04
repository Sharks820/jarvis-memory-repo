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

    // ── Learning mission endpoints ────────────────────────────────────

    /** Create a learning mission on the desktop. */
    @POST("/missions/create")
    suspend fun createMission(@Body request: MissionCreateRequest): MissionCreateResponse

    /** Get learning mission status from desktop. */
    @GET("/missions/status")
    suspend fun getMissionStatus(): MissionStatusResponse

    // ── Automation endpoints ────────────────────────────────────────────

    /** Drain all pending proactive alerts (clears the queue). */
    @GET("/alerts/pending")
    suspend fun getAlertsPending(): AlertsPendingResponse

    /** Get a context-aware digest of what happened while user was busy. */
    @GET("/digest")
    suspend fun getDigest(
        @retrofit2.http.Query("since") since: Long = 0,
        @retrofit2.http.Query("context") context: String = "",
    ): DigestResponse

    /** Get KG-powered intelligence briefing for an upcoming meeting. */
    @GET("/meeting-prep")
    suspend fun getMeetingPrep(
        @retrofit2.http.Query("title") title: String = "",
        @retrofit2.http.Query("attendees") attendees: String = "",
    ): MeetingPrepResponse

    /** Generate a contextual auto-reply SMS for a missed call. */
    @POST("/smart-reply")
    suspend fun getSmartReply(@Body request: SmartReplyRequest): SmartReplyResponse

    // ── Intelligence sync endpoints ─────────────────────────────────────

    /**
     * Push phone-learned intelligence to the desktop for merging into
     * the knowledge graph. The phone sends context observations, habit
     * patterns, and locally-learned facts.
     */
    @POST("/intelligence/merge")
    suspend fun intelligenceMerge(@Body body: Map<String, Any>): IntelligenceMergeResponse

    /**
     * Pull desktop knowledge for the phone's local intelligence store.
     * Returns structured facts from the knowledge graph and memory.
     */
    @POST("/intelligence/export")
    suspend fun intelligenceExport(@Body body: Map<String, Any>): IntelligenceExportResponse
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

/** Response from /intelligence/merge. */
data class IntelligenceMergeResponse(
    val ok: Boolean = false,
    val merged: Int = 0,
    val total_received: Int = 0,
)

/** Response from /intelligence/export. */
data class IntelligenceExportResponse(
    val ok: Boolean = false,
    val items: List<Map<String, Any?>> = emptyList(),
    val total: Int = 0,
)

/** Request body for POST /missions/create. */
data class MissionCreateRequest(
    val topic: String,
    val objective: String = "",
    val sources: List<String> = emptyList(),
)

/** Response from POST /missions/create. */
data class MissionCreateResponse(
    val ok: Boolean = false,
    @com.google.gson.annotations.SerializedName("mission_id")
    val missionId: String = "",
    val topic: String = "",
    val status: String = "",
    val origin: String = "",
    val sources: List<String> = emptyList(),
)

/** Individual mission entry from GET /missions/status. */
data class MissionDto(
    @com.google.gson.annotations.SerializedName("mission_id")
    val missionId: String = "",
    val topic: String = "",
    val objective: String = "",
    val status: String = "",
    val origin: String = "",
    val sources: List<String> = emptyList(),
    @com.google.gson.annotations.SerializedName("verified_findings")
    val verifiedFindings: Int = 0,
    @com.google.gson.annotations.SerializedName("created_utc")
    val createdUtc: String = "",
    @com.google.gson.annotations.SerializedName("updated_utc")
    val updatedUtc: String = "",
)

/** Response from GET /missions/status. */
data class MissionStatusResponse(
    val ok: Boolean = false,
    val total: Int = 0,
    val missions: List<MissionDto> = emptyList(),
)

// ── Automation response models ──────────────────────────────────────────

/** Response from GET /alerts/pending. */
data class AlertsPendingResponse(
    val ok: Boolean = false,
    val alerts: List<PendingAlert> = emptyList(),
)

data class PendingAlert(
    val id: String = "",
    val type: String = "",
    val title: String = "",
    val body: String = "",
    @com.google.gson.annotations.SerializedName("group_key")
    val groupKey: String = "",
    val priority: String = "",
    val ts: Double = 0.0,
)

/** Request for POST /smart-reply. */
data class SmartReplyRequest(
    @com.google.gson.annotations.SerializedName("contact_name")
    val contactName: String,
    @com.google.gson.annotations.SerializedName("phone_number")
    val phoneNumber: String = "",
    val context: String = "",
    @com.google.gson.annotations.SerializedName("meeting_end_time")
    val meetingEndTime: String = "",
    @com.google.gson.annotations.SerializedName("eta_minutes")
    val etaMinutes: Int? = null,
)

/** Response from POST /smart-reply. */
data class SmartReplyResponse(
    val ok: Boolean = false,
    val reply: String = "",
    @com.google.gson.annotations.SerializedName("contact_context")
    val contactContext: String = "",
)

/** Response from GET /digest. */
data class DigestResponse(
    val ok: Boolean = false,
    val digest: DigestData? = null,
)

data class DigestData(
    val context: String = "",
    @com.google.gson.annotations.SerializedName("since_ts")
    val sinceTs: Long = 0,
    @com.google.gson.annotations.SerializedName("missed_calls")
    val missedCalls: List<Map<String, Any?>> = emptyList(),
    @com.google.gson.annotations.SerializedName("notifications_summary")
    val notificationsSummary: String = "",
    @com.google.gson.annotations.SerializedName("calendar_upcoming")
    val calendarUpcoming: List<Map<String, Any?>> = emptyList(),
    @com.google.gson.annotations.SerializedName("proactive_alerts")
    val proactiveAlerts: List<Map<String, Any?>> = emptyList(),
)

/** Response from GET /meeting-prep. */
data class MeetingPrepResponse(
    val ok: Boolean = false,
    val briefing: MeetingBriefing? = null,
)

data class MeetingBriefing(
    val title: String = "",
    val attendees: List<String> = emptyList(),
    @com.google.gson.annotations.SerializedName("context_facts")
    val contextFacts: List<ContextFact> = emptyList(),
    @com.google.gson.annotations.SerializedName("recent_memories")
    val recentMemories: List<RecentMemory> = emptyList(),
    @com.google.gson.annotations.SerializedName("suggested_topics")
    val suggestedTopics: List<String> = emptyList(),
)

data class ContextFact(
    val about: String = "",
    val fact: String = "",
    val confidence: Double = 0.0,
)

data class RecentMemory(
    val about: String = "",
    val summary: String = "",
    val date: String = "",
)
