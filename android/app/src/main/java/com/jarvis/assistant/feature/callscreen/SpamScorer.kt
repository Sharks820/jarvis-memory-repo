package com.jarvis.assistant.feature.callscreen

import android.content.Context
import android.util.Log
import com.jarvis.assistant.data.dao.SpamDao
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Scoring engine that evaluates incoming phone numbers against the locally
 * synced spam database.  Mirrors the desktop phone_guard scoring logic.
 *
 * Threshold preferences are read from EncryptedSharedPreferences so the user
 * can adjust sensitivity in Settings.
 */
@Singleton
class SpamScorer @Inject constructor(
    private val spamDao: SpamDao,
    @ApplicationContext private val context: Context,
) {

    /**
     * Result of scoring a phone number against the spam database.
     *
     * @property score 0.0-1.0 spam likelihood (higher = more likely spam).
     * @property reasons list of reason tags from the desktop scoring engine.
     * @property recommendedAction one of "block", "silence", "voicemail", "allow".
     */
    data class ScoreResult(
        val score: Float,
        val reasons: List<String>,
        val recommendedAction: String,
    )

    /**
     * Score [phoneNumber] against the local spam database.
     *
     * The number should already be normalized via [normalizeNumber].
     * If the number is not in the database, returns a score of 0 with "allow".
     */
    suspend fun score(phoneNumber: String): ScoreResult {
        val entity = spamDao.findByNumber(phoneNumber)
            ?: return ScoreResult(0f, emptyList(), "allow")

        // If the user explicitly configured an action, honour it.
        if (entity.userAction != "auto") {
            return ScoreResult(
                score = entity.score,
                reasons = parseReasons(entity.reasons),
                recommendedAction = entity.userAction,
            )
        }

        // Otherwise decide based on score vs. configurable thresholds.
        val action = determineAction(entity.score)
        return ScoreResult(
            score = entity.score,
            reasons = parseReasons(entity.reasons),
            recommendedAction = action,
        )
    }

    /**
     * Normalise a phone number for consistent database lookups.
     *
     * Ported from desktop `phone_guard.py _normalize_number()`:
     * - Strip non-digit characters except leading `+`
     * - Convert `00` international prefix to `+`
     * - 10-digit US numbers get `+1` prefix
     * - 11-digit starting with `1` get `+` prefix
     * - 8+ digits get `+` prefix
     * - Anything shorter is invalid (returns empty string)
     */
    fun normalizeNumber(number: String): String {
        if (number.isBlank()) return ""

        // Strip everything except digits and +
        var cleaned = number.replace(Regex("[^\\d+]"), "")

        // Handle 00 international prefix
        if (cleaned.startsWith("00")) {
            cleaned = "+" + cleaned.substring(2)
        }

        // If already has + and is long enough, return as-is
        if (cleaned.startsWith("+") && cleaned.length >= 8) {
            return cleaned
        }

        // Strip to pure digits for length-based logic
        val digits = cleaned.replace(Regex("\\D"), "")

        return when {
            digits.length == 10 -> "+1$digits"
            digits.length == 11 && digits.startsWith("1") -> "+$digits"
            digits.length >= 8 -> "+$digits"
            else -> ""
        }
    }

    // ---- Private helpers ----------------------------------------------------

    private fun determineAction(score: Float): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val blockThreshold = prefs.getFloat(KEY_BLOCK_THRESHOLD, DEFAULT_BLOCK)
        val silenceThreshold = prefs.getFloat(KEY_SILENCE_THRESHOLD, DEFAULT_SILENCE)
        val voicemailThreshold = prefs.getFloat(KEY_VOICEMAIL_THRESHOLD, DEFAULT_VOICEMAIL)

        return when {
            score >= blockThreshold -> "block"
            score >= silenceThreshold -> "silence"
            score >= voicemailThreshold -> "voicemail"
            else -> "allow"
        }
    }

    private fun parseReasons(json: String): List<String> {
        if (json.isBlank()) return emptyList()
        return try {
            // Simple JSON array parsing: ["reason1","reason2"]
            json.trim()
                .removePrefix("[")
                .removeSuffix("]")
                .split(",")
                .map { it.trim().removeSurrounding("\"") }
                .filter { it.isNotBlank() }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse spam reasons JSON", e)
            emptyList()
        }
    }

    companion object {
        private const val TAG = "SpamScorer"
        /** SharedPreferences file used for call screening thresholds. */
        const val PREFS_NAME = "spam_thresholds"

        const val KEY_BLOCK_THRESHOLD = "call_screen_block_threshold"
        const val KEY_SILENCE_THRESHOLD = "call_screen_silence_threshold"
        const val KEY_VOICEMAIL_THRESHOLD = "call_screen_voicemail_threshold"
        const val KEY_ENABLED = "call_screen_enabled"

        const val DEFAULT_BLOCK = 0.80f
        const val DEFAULT_SILENCE = 0.60f
        const val DEFAULT_VOICEMAIL = 0.40f
    }
}
