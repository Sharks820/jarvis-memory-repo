package com.jarvis.assistant.api.models

import com.google.gson.annotations.SerializedName

/** Request body for POST /command */
data class CommandRequest(
    val text: String,
    val execute: Boolean = false,
    @SerializedName("approve_privileged") val approvePrivileged: Boolean = false,
    val speak: Boolean = false,
)

/** Response from POST /command */
data class CommandResponse(
    val ok: Boolean = false,
    val intent: String = "",
    @SerializedName("stdout_tail") val stdoutTail: List<String> = emptyList(),
)

/** Response from GET /settings */
data class SettingsResponse(
    val settings: SettingsData? = null,
)

data class SettingsData(
    @SerializedName("runtime_control") val runtimeControl: RuntimeControl? = null,
    @SerializedName("gaming_mode") val gamingMode: GamingMode? = null,
)

data class RuntimeControl(
    @SerializedName("daemon_paused") val daemonPaused: Boolean = false,
    @SerializedName("safe_mode") val safeMode: Boolean = false,
)

data class GamingMode(
    val enabled: Boolean = false,
)

/** Response from GET /dashboard */
data class DashboardResponse(
    val dashboard: DashboardData? = null,
)

data class DashboardData(
    val jarvis: JarvisScore? = null,
    val ranking: List<RankingEntry> = emptyList(),
    val etas: List<EtaEntry> = emptyList(),
    @SerializedName("memory_regression") val memoryRegression: MemoryRegression? = null,
)

data class JarvisScore(
    @SerializedName("score_pct") val scorePct: Int = 0,
    @SerializedName("delta_vs_prev_pct") val deltaPct: Int = 0,
    @SerializedName("latest_model") val latestModel: String = "",
)

data class RankingEntry(
    val name: String = "",
    @SerializedName("score_pct") val scorePct: Int = 0,
)

data class EtaEntry(
    @SerializedName("target_name") val targetName: String = "",
    val eta: EtaInfo? = null,
)

data class EtaInfo(
    val status: String = "",
    val runs: Int? = null,
    val days: Int? = null,
)

data class MemoryRegression(
    val status: String = "",
    @SerializedName("duplicate_ratio") val duplicateRatio: Double = 0.0,
    @SerializedName("unresolved_conflicts") val unresolvedConflicts: Int = 0,
)

/** POST /bootstrap response */
data class BootstrapResponse(
    val ok: Boolean = false,
    val session: BootstrapSession? = null,
    val message: String = "",
)

/** Nested session credentials from bootstrap response */
data class BootstrapSession(
    @SerializedName("base_url") val baseUrl: String = "",
    val token: String = "",
    @SerializedName("signing_key") val signingKey: String = "",
    @SerializedName("device_id") val deviceId: String = "",
    @SerializedName("trusted_device") val trustedDevice: Boolean = false,
)

/** Health check response */
data class HealthResponse(
    val status: String = "",
)

// ── Spam / Call Screening ────────────────────────────────────────────

/** Response from GET /spam/candidates (future desktop endpoint). */
data class SpamCandidatesResponse(
    val ok: Boolean = false,
    val candidates: List<SpamCandidateDto> = emptyList(),
)

/** Individual spam candidate as reported by the desktop phone_guard module. */
data class SpamCandidateDto(
    val number: String = "",
    val score: Float = 0f,
    val calls: Int = 0,
    @SerializedName("missed_ratio") val missedRatio: Float = 0f,
    @SerializedName("avg_duration_s") val avgDurationS: Float = 0f,
    val reasons: List<String> = emptyList(),
)

// ── Proactive Notifications ──────────────────────────────────────────

/** Response from a future dedicated proactive alerts endpoint. */
data class ProactiveAlertsResponse(
    val ok: Boolean = false,
    val alerts: List<ProactiveAlertDto> = emptyList(),
)

/** Individual proactive alert DTO from the desktop engine. */
data class ProactiveAlertDto(
    val id: String = "",
    val type: String = "",
    val title: String = "",
    val body: String = "",
    @SerializedName("group_key") val groupKey: String = "",
)

// ── TLS Certificate ─────────────────────────────────────────────────

/** Response from GET /cert-fingerprint for TOFU cert pinning. */
data class CertFingerprintResponse(
    val ok: Boolean = false,
    val fingerprint: String = "",
    val algorithm: String = "sha256",
)

// ── Scheduling / Conflict Detection ─────────────────────────────────

/** Response model for calendar conflict checking (future desktop endpoint). */
data class ConflictCheckResponse(
    val ok: Boolean = false,
    val conflicts: List<String> = emptyList(),
)
