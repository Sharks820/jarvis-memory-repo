package com.jarvis.assistant.sync

import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Caches command responses locally so the phone can serve answers
 * even when the desktop is completely unreachable.
 *
 * This is what makes Jarvis useful on the phone WITHOUT the desktop:
 * - Every successful command response gets cached with its query
 * - When offline, similar queries can be matched against cached responses
 * - Cached knowledge from the last sync is available for reference
 * - Cache entries have TTL and are evicted when max capacity is reached
 *
 * The cache is a simple JSON file stored in the app's internal storage.
 * It's intentionally simple — the phone doesn't need to be a full LLM,
 * it just needs to remember what the desktop told it recently.
 */
@Singleton
class LocalResponseCache @Inject constructor(
    @ApplicationContext private val context: Context,
    private val syncConfig: SyncConfigStore,
) {
    private val cacheFile: File
        get() = File(context.filesDir, "response_cache.json")

    private val lock = Any()

    /**
     * Cache a command response for offline retrieval.
     *
     * @param query The user's original command text
     * @param response The desktop's response text
     * @param category Optional category for better matching (e.g., "weather", "schedule")
     */
    fun cacheResponse(query: String, response: String, category: String = "") {
        if (!syncConfig.cacheResponses) return
        if (query.isBlank() || response.isBlank()) return

        synchronized(lock) {
            try {
                val entries = loadEntries()

                // Add new entry
                val entry = JSONObject().apply {
                    put("query", query.lowercase().trim())
                    put("response", response)
                    put("category", category)
                    put("timestamp", System.currentTimeMillis())
                }
                entries.put(entry)

                // Evict expired entries and enforce max capacity
                val cleaned = evictStale(entries)
                saveEntries(cleaned)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to cache response: ${e.message}")
            }
        }
    }

    /**
     * Find a cached response that matches the query.
     *
     * Uses simple keyword matching — not LLM-level understanding, but good
     * enough for common repeated queries like "what's my schedule", "weather",
     * "remind me about X", etc.
     *
     * @return The cached response text, or null if no match found
     */
    fun findCachedResponse(query: String): String? {
        if (!syncConfig.cacheResponses) return null

        synchronized(lock) {
            try {
                val entries = loadEntries()
                val normalizedQuery = query.lowercase().trim()
                val queryWords = normalizedQuery.split("\\s+".toRegex()).filter { it.length > 2 }

                if (queryWords.isEmpty()) return null

                var bestMatch: JSONObject? = null
                var bestScore = 0.0

                for (i in 0 until entries.length()) {
                    val entry = entries.getJSONObject(i)
                    val cachedQuery = entry.getString("query")
                    val timestamp = entry.getLong("timestamp")

                    // Skip expired entries
                    val ageHours = (System.currentTimeMillis() - timestamp) / (1000 * 60 * 60)
                    if (ageHours > syncConfig.cacheTtlHours) continue

                    // Score: percentage of query words found in cached query
                    val cachedWords = cachedQuery.split("\\s+".toRegex()).toSet()
                    val matchCount = queryWords.count { word ->
                        cachedWords.any { it.contains(word) || word.contains(it) }
                    }
                    val score = matchCount.toDouble() / queryWords.size

                    // Exact match bonus
                    val exactBonus = if (cachedQuery == normalizedQuery) 1.0 else 0.0

                    val totalScore = score + exactBonus

                    if (totalScore > bestScore && totalScore >= 0.5) {
                        bestScore = totalScore
                        bestMatch = entry
                    }
                }

                return bestMatch?.getString("response")?.let { response ->
                    val timestamp = bestMatch!!.getLong("timestamp")
                    val ageMinutes = (System.currentTimeMillis() - timestamp) / (1000 * 60)
                    val ageLabel = when {
                        ageMinutes < 60 -> "${ageMinutes}m ago"
                        ageMinutes < 1440 -> "${ageMinutes / 60}h ago"
                        else -> "${ageMinutes / 1440}d ago"
                    }
                    "[Cached response from $ageLabel — desktop offline]\n\n$response"
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to find cached response: ${e.message}")
                return null
            }
        }
    }

    /** Clear the entire cache. */
    fun clear() {
        synchronized(lock) {
            try {
                cacheFile.delete()
            } catch (e: Exception) {
                Log.w(TAG, "Failed to clear response cache: ${e.message}")
            }
        }
    }

    /** Return the number of cached entries. */
    fun size(): Int {
        synchronized(lock) {
            return try {
                loadEntries().length()
            } catch (e: Exception) {
                0
            }
        }
    }

    private fun loadEntries(): JSONArray {
        return try {
            if (cacheFile.exists()) {
                JSONArray(cacheFile.readText())
            } else {
                JSONArray()
            }
        } catch (e: Exception) {
            JSONArray()
        }
    }

    private fun saveEntries(entries: JSONArray) {
        cacheFile.writeText(entries.toString())
    }

    private fun evictStale(entries: JSONArray): JSONArray {
        val now = System.currentTimeMillis()
        val ttlMs = syncConfig.cacheTtlHours * 60 * 60 * 1000
        val maxEntries = syncConfig.cacheMaxEntries

        // Collect valid (non-expired) entries
        val valid = mutableListOf<JSONObject>()
        for (i in 0 until entries.length()) {
            val entry = entries.getJSONObject(i)
            val age = now - entry.getLong("timestamp")
            if (age < ttlMs) {
                valid.add(entry)
            }
        }

        // If still over capacity, remove oldest entries
        if (valid.size > maxEntries) {
            valid.sortByDescending { it.getLong("timestamp") }
            val trimmed = valid.take(maxEntries)
            val result = JSONArray()
            trimmed.forEach { result.put(it) }
            return result
        }

        val result = JSONArray()
        valid.forEach { result.put(it) }
        return result
    }

    companion object {
        private const val TAG = "ResponseCache"
    }
}
