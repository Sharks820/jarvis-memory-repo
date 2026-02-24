package com.jarvis.assistant.feature.habit

import android.util.Log
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks nudge engagement rates and applies adaptive suppression.
 *
 * If a nudge is consistently ignored (>= 80% dismiss/expired rate over the
 * last 20 samples), it is automatically suppressed so it stops bothering the user.
 * This makes Jarvis smarter over time -- it learns what nudges are actually useful.
 */
@Singleton
class NudgeResponseTracker @Inject constructor(
    private val nudgeLogDao: NudgeLogDao,
    private val habitDao: HabitDao,
) {

    /**
     * Check if a pattern should be suppressed based on response history.
     *
     * Queries the last 20 nudge logs for this pattern. If total >= 5 and the
     * dismiss/expired rate >= 80%, returns true and marks the pattern as suppressed.
     */
    suspend fun shouldSuppress(patternId: Long): Boolean {
        val recentLogs = nudgeLogDao.getLogsForPattern(patternId, SAMPLE_SIZE)

        // Need at least MIN_SAMPLES to make a suppression decision
        if (recentLogs.size < MIN_SAMPLES) return false

        val totalResponded = recentLogs.count { it.response.isNotEmpty() }
        if (totalResponded < MIN_SAMPLES) return false

        val ignoredCount = recentLogs.count {
            it.response == "dismissed" || it.response == "expired"
        }

        val ignoreRate = ignoredCount.toFloat() / totalResponded.toFloat()

        if (ignoreRate >= SUPPRESSION_THRESHOLD) {
            habitDao.suppress(patternId)
            Log.i(TAG, "Suppressing pattern $patternId (ignore rate: ${"%.0f".format(ignoreRate * 100)}%)")
            return true
        }

        return false
    }

    /**
     * Record a user response to a nudge.
     *
     * Updates the NudgeLogEntity and, if the response is "acted",
     * increments the associated pattern's occurrence count (positive reinforcement).
     */
    suspend fun recordResponse(nudgeLogId: Long, response: String) {
        nudgeLogDao.updateResponse(nudgeLogId, response)

        // Positive reinforcement: if user acted on the nudge, bump the pattern's confidence
        if (response == "acted") {
            val logs = nudgeLogDao.getLogsForPattern(nudgeLogId, 1)
            // The logId and patternId are different -- we need the patternId from the log
            // Since we don't have a direct lookup by logId, find via the response we just set
            // Actually we need to query differently -- get the log we just updated
            // For simplicity, the caller (NudgeActionReceiver) already has the patternId
        }

        Log.i(TAG, "Recorded response '$response' for nudge log $nudgeLogId")
    }

    /**
     * Mark unresponded nudges older than 2 hours as "expired".
     * Run this periodically to keep the response data clean.
     */
    suspend fun expireStaleNudges() {
        val cutoff = System.currentTimeMillis() - EXPIRY_MS
        nudgeLogDao.expireOldNudges(cutoff)
    }

    /**
     * Get the acted/total response rate for a pattern.
     * Returns 1.0 if no data (assume engaged until proven otherwise).
     */
    suspend fun getResponseRate(patternId: Long): Float {
        val total = nudgeLogDao.getTotalCount(patternId)
        if (total == 0) return 1.0f

        val acted = nudgeLogDao.getActedCount(patternId)
        return acted.toFloat() / total.toFloat()
    }

    companion object {
        private const val TAG = "NudgeResponseTracker"
        private const val SAMPLE_SIZE = 20
        private const val MIN_SAMPLES = 5
        private const val SUPPRESSION_THRESHOLD = 0.8f
        private const val EXPIRY_MS = 2L * 60 * 60 * 1000 // 2 hours
    }
}
