package com.jarvis.assistant.feature.callscreen

import android.util.Log
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.api.models.SpamCandidateDto
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.entity.SpamEntity
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Manages synchronisation of the local spam number database with the
 * desktop engine's phone_guard module.
 *
 * The desktop scoring engine (`detect_spam_candidates`) runs periodically
 * and produces a spam report.  This class pulls those pre-computed candidates
 * via the /command endpoint and upserts them into the local Room database.
 */
@Singleton
class SpamDatabaseSync @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val spamDao: SpamDao,
) {

    private val gson = Gson()

    /**
     * Pull the latest spam candidates from the desktop engine and upsert
     * them into the local Room database.
     *
     * Strategy:
     * 1. Send "Jarvis, run spam scan" (execute=true) to trigger a fresh scan.
     * 2. Send "Jarvis, show spam report" to retrieve the current report.
     * 3. Parse the stdout_tail for JSON spam candidate data.
     * 4. Upsert parsed candidates into SpamDao.
     * 5. Delete entries not synced in the last 7 days.
     *
     * If parsing fails, existing local data is kept (graceful degradation).
     */
    suspend fun syncFromDesktop() {
        try {
            // Step 1: Trigger a fresh spam scan on the desktop
            try {
                apiClient.api().sendCommand(
                    CommandRequest(text = "Jarvis, run spam scan", execute = true),
                )
            } catch (e: Exception) {
                Log.w(TAG, "Spam scan trigger failed (non-fatal): ${e.message}")
            }

            // Step 2: Retrieve the spam report
            val reportResponse = apiClient.api().sendCommand(
                CommandRequest(text = "Jarvis, show spam report"),
            )

            if (!reportResponse.ok) {
                Log.w(TAG, "Spam report request returned ok=false")
                return
            }

            // Step 3: Parse candidates from stdout_tail
            val candidates = parseCandidates(reportResponse.stdoutTail)
            if (candidates.isEmpty()) {
                Log.d(TAG, "No spam candidates parsed from desktop response")
                return
            }

            // Step 4: Upsert into local DB
            val now = System.currentTimeMillis()
            val entities = candidates.map { dto ->
                SpamEntity(
                    number = dto.number,
                    score = dto.score,
                    calls = dto.calls,
                    missedRatio = dto.missedRatio,
                    avgDurationS = dto.avgDurationS,
                    reasons = gson.toJson(dto.reasons),
                    lastSynced = now,
                )
            }
            spamDao.upsertAll(entities)

            // Step 5: Clean up entries not synced in the last 7 days
            val sevenDaysAgo = now - STALE_CUTOFF_MS
            spamDao.deleteStale(sevenDaysAgo)

            Log.i(TAG, "Spam DB sync complete: ${entities.size} candidates upserted")
        } catch (e: Exception) {
            Log.w(TAG, "Spam DB sync error (keeping existing data): ${e.message}")
        }
    }

    // ---- Parsing helpers ----------------------------------------------------

    /**
     * Attempt to parse spam candidates from the stdout_tail lines.
     *
     * The desktop spam report can come in several formats:
     * 1. A single JSON line containing a "candidates" array
     * 2. Individual JSON lines, each representing a candidate
     * 3. A full report JSON with nested "candidates" key
     */
    internal fun parseCandidates(lines: List<String>): List<SpamCandidateDto> {
        if (lines.isEmpty()) return emptyList()

        // Try joining all lines as a single JSON blob with "candidates" key
        val joined = lines.joinToString("\n").trim()
        try {
            val reportType = object : TypeToken<Map<String, Any>>() {}.type
            val report: Map<String, Any> = gson.fromJson(joined, reportType)
            val candidatesJson = gson.toJson(report["candidates"])
            val listType = object : TypeToken<List<SpamCandidateDto>>() {}.type
            val parsed: List<SpamCandidateDto> = gson.fromJson(candidatesJson, listType)
            if (parsed.isNotEmpty()) return parsed
        } catch (_: Exception) {
            // Not a report-format JSON
        }

        // Try parsing each line as an individual candidate
        val candidates = mutableListOf<SpamCandidateDto>()
        for (line in lines) {
            val trimmed = line.trim()
            if (!trimmed.startsWith("{")) continue
            try {
                val candidate = gson.fromJson(trimmed, SpamCandidateDto::class.java)
                if (candidate.number.isNotBlank()) {
                    candidates.add(candidate)
                }
            } catch (_: Exception) {
                // Skip unparseable lines
            }
        }

        // Try parsing joined lines as a JSON array of candidates
        if (candidates.isEmpty()) {
            try {
                val listType = object : TypeToken<List<SpamCandidateDto>>() {}.type
                val parsed: List<SpamCandidateDto> = gson.fromJson(joined, listType)
                if (parsed.isNotEmpty()) return parsed
            } catch (_: Exception) {
                // Not an array
            }
        }

        return candidates
    }

    companion object {
        private const val TAG = "SpamDBSync"
        /** 7 days in milliseconds. */
        private const val STALE_CUTOFF_MS = 7L * 24 * 60 * 60 * 1000
    }
}
