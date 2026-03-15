package com.jarvis.assistant.intelligence

import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.TransactionDao
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Bidirectional intelligence merger — makes both brains smarter together.
 *
 * This is NOT data sync (that's handled by the sync engine). This is
 * INTELLIGENCE sync — synthesized knowledge, learned patterns, and
 * reasoned conclusions that each brain discovered independently.
 *
 * **Phone → Desktop:**
 * - Context observations (driving patterns, meeting attendance, sleep schedule)
 * - Contact interaction patterns (who you talk to, when, how often)
 * - Spending patterns and anomalies detected locally
 * - Habit patterns detected from phone sensors
 * - Local knowledge facts learned from Gemini Nano interactions
 *
 * **Desktop → Phone:**
 * - Knowledge graph facts (structured knowledge from all sources)
 * - Learning summaries (what the desktop learned from analysis)
 * - User preference models (how you like things, communication style)
 * - Cross-source insights (things only visible with all data combined)
 *
 * Each merge makes BOTH systems more intelligent. The phone's real-world
 * observations enrich the desktop's knowledge, and the desktop's deep
 * analysis enriches the phone's local intelligence.
 */
@Singleton
class IntelligenceMerger @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val knowledgeStore: LocalKnowledgeStore,
    private val contextStateDao: ContextStateDao,
    private val contactContextDao: ContactContextDao,
    private val habitDao: HabitDao,
    private val transactionDao: TransactionDao,
) {
    /**
     * Push phone intelligence to the desktop.
     *
     * Exports locally-learned facts and phone-only observations
     * to the desktop for incorporation into the full knowledge graph.
     *
     * @return Number of intelligence items synced
     */
    suspend fun pushPhoneIntelligence(): Int {
        try {
            val items = mutableListOf<Map<String, Any>>()

            // 1. Unsynced knowledge facts from local learning
            val unsyncedFacts = knowledgeStore.exportUnsyncedFacts()
            items.addAll(unsyncedFacts)

            // 2. Recent context observations (what phone detected about user's behavior)
            try {
                val weekAgo = System.currentTimeMillis() - 7 * 24 * 60 * 60 * 1000
                val recentContext = contextStateDao.getStatesSince(weekAgo)
                for (ctx in recentContext.takeLast(20)) {
                    items.add(mapOf(
                        "content" to "Context: ${ctx.context} detected at ${ctx.createdAt} " +
                            "(confidence: ${ctx.confidence}, source: ${ctx.source})",
                        "category" to "context",
                        "confidence" to ctx.confidence.toDouble(),
                        "source" to "phone_sensor",
                        "timestamp" to ctx.createdAt,
                    ))
                }
            } catch (e: Exception) {
                Log.d(TAG, "Context export failed: ${e.message}")
            }

            // 3. Habit patterns detected on phone
            try {
                val habits = habitDao.getActivePatterns()
                for (habit in habits) {
                    items.add(mapOf(
                        "content" to "Habit: ${habit.label} — ${habit.description} " +
                            "(${habit.patternType}, confidence: ${habit.confidence}, " +
                            "occurrences: ${habit.occurrenceCount})",
                        "category" to "habit",
                        "confidence" to habit.confidence.toDouble(),
                        "source" to "phone_pattern",
                        "timestamp" to habit.updatedAt,
                    ))
                }
            } catch (e: Exception) {
                Log.d(TAG, "Habit export failed: ${e.message}")
            }

            if (items.isEmpty()) {
                Log.d(TAG, "No phone intelligence to push")
                return 0
            }

            // Send to desktop via dedicated intelligence/merge endpoint
            val response = apiClient.api().intelligenceMerge(
                mapOf("items" to items),
            )
            val count = if (response.ok) response.merged else 0
            Log.i(TAG, "Pushed $count intelligence items to desktop")

            // Mark facts as synced ONLY after network success
            if (response.ok && unsyncedFacts.isNotEmpty()) {
                knowledgeStore.markFactsSynced()
            }

            return count
        } catch (e: Exception) {
            Log.w(TAG, "Push phone intelligence failed: ${e.message}")
            return 0
        }
    }

    /**
     * Pull desktop intelligence to the phone.
     *
     * Fetches knowledge graph facts, learning summaries, and preference
     * models from the desktop and imports them into the local knowledge store.
     *
     * @return Number of intelligence items received
     */
    suspend fun pullDesktopIntelligence(): Int {
        try {
            // Request knowledge export from desktop via dedicated endpoint
            val response = apiClient.api().intelligenceExport(
                mapOf("limit" to 200),
            )
            if (!response.ok) return 0

            // Convert items to the format expected by knowledge store
            val facts = response.items.mapNotNull { item ->
                val content = item["content"]?.toString() ?: return@mapNotNull null
                mapOf(
                    "content" to content,
                    "category" to (item["category"]?.toString() ?: LocalKnowledgeStore.CAT_DESKTOP),
                    "confidence" to ((item["confidence"] as? Number)?.toDouble() ?: 0.7),
                    "keywords" to (item["keywords"]?.toString() ?: ""),
                )
            }
            if (facts.isNotEmpty()) {
                knowledgeStore.importDesktopFacts(facts)
                Log.i(TAG, "Pulled ${facts.size} intelligence items from desktop")
            }

            return facts.size
        } catch (e: Exception) {
            Log.w(TAG, "Pull desktop intelligence failed: ${e.message}")
            return 0
        }
    }

    /**
     * Full bidirectional intelligence merge.
     *
     * Call this on reconnection to ensure both brains are up to date.
     */
    suspend fun fullMerge(): MergeResult {
        val pushed = pushPhoneIntelligence()
        val pulled = pullDesktopIntelligence()
        Log.i(TAG, "Full intelligence merge: pushed=$pushed, pulled=$pulled")
        return MergeResult(pushed = pushed, pulled = pulled)
    }

    data class MergeResult(val pushed: Int, val pulled: Int)

    companion object {
        private const val TAG = "IntelMerger"
    }
}
