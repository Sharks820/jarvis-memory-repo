package com.jarvis.assistant.intelligence

import android.content.Context
import android.util.Log
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.TransactionDao
import dagger.hilt.android.qualifiers.ApplicationContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Local knowledge store for on-device intelligence.
 *
 * This is the phone's own knowledge graph — a persistent, searchable store of
 * facts, learned information, and intelligence synced from the desktop.
 *
 * NOT a cache of responses. This is structured knowledge:
 * - Facts about Conner (preferences, relationships, schedule patterns)
 * - Facts from the desktop's knowledge graph (synced periodically)
 * - Facts learned locally from phone interactions (context, calls, spending)
 * - Conversation summaries for long-term memory
 * - User preferences and behavioral patterns
 *
 * The knowledge store supports:
 * - **Keyword search**: Find facts by keyword matching
 * - **Category-based retrieval**: Get all facts in a domain (health, finance, etc.)
 * - **Reasoning**: Combine multiple facts to answer complex questions
 * - **Learning**: Add new facts from phone interactions
 * - **Sync**: Import/export facts for desktop synchronization
 */
@Singleton
class LocalKnowledgeStore @Inject constructor(
    @ApplicationContext private val context: Context,
    private val conversationDao: ConversationDao,
    private val contactContextDao: ContactContextDao,
    private val contextStateDao: ContextStateDao,
    private val extractedEventDao: ExtractedEventDao,
    private val habitDao: HabitDao,
    private val medicationDao: MedicationDao,
    private val transactionDao: TransactionDao,
) {
    private val knowledgeFile: File
        get() = File(context.filesDir, "knowledge_store.json")

    private val lock = Any()

    // Categories for knowledge organization
    companion object {
        const val CAT_PERSONAL = "personal"     // Name, preferences, etc.
        const val CAT_HEALTH = "health"         // Medications, conditions
        const val CAT_FINANCE = "finance"       // Spending patterns, budgets
        const val CAT_SOCIAL = "social"         // Relationships, contacts
        const val CAT_SCHEDULE = "schedule"     // Routines, events
        const val CAT_LOCATION = "location"     // Places, commute
        const val CAT_WORK = "work"             // Work-related facts
        const val CAT_GENERAL = "general"       // Everything else
        const val CAT_DESKTOP = "desktop"       // Synced from desktop KG
        private const val TAG = "KnowledgeStore"
        private const val MAX_FACTS = 2000
    }

    /**
     * Search for facts relevant to a query.
     *
     * Uses keyword matching across fact content and categories.
     * Returns the most relevant facts, sorted by relevance score.
     */
    fun searchFacts(query: String, limit: Int = 10): List<String> {
        synchronized(lock) {
            try {
                val facts = loadFacts()
                val queryWords = query.lowercase().split("\\s+".toRegex())
                    .filter { it.length > 2 }

                if (queryWords.isEmpty()) return emptyList()

                data class ScoredFact(val content: String, val score: Double)

                val scored = mutableListOf<ScoredFact>()

                for (i in 0 until facts.length()) {
                    val fact = facts.getJSONObject(i)
                    val content = fact.optString("content", "").lowercase()
                    val category = fact.optString("category", "")
                    val keywords = fact.optString("keywords", "").lowercase()
                    val searchable = "$content $category $keywords"

                    var score = 0.0
                    for (word in queryWords) {
                        if (searchable.contains(word)) {
                            score += 1.0
                            // Bonus for exact keyword match
                            if (keywords.contains(word)) score += 0.5
                        }
                    }

                    // Recency bonus
                    val ts = fact.optLong("timestamp", 0)
                    val ageHours = (System.currentTimeMillis() - ts) / (1000.0 * 60 * 60)
                    if (ageHours < 24) score += 0.3
                    else if (ageHours < 168) score += 0.1

                    // Confidence bonus
                    val confidence = fact.optDouble("confidence", 0.5)
                    score *= confidence

                    if (score > 0.3) {
                        scored.add(ScoredFact(fact.optString("content", ""), score))
                    }
                }

                return scored.sortedByDescending { it.score }
                    .take(limit)
                    .map { it.content }
            } catch (e: Exception) {
                Log.w(TAG, "Search failed: ${e.message}")
                return emptyList()
            }
        }
    }

    /**
     * Use local knowledge to reason about a query.
     *
     * This is the knowledge engine fallback when Gemini Nano is unavailable.
     * It's not generative AI — it's structured reasoning over known facts:
     * - Pattern matching against stored knowledge
     * - Temporal reasoning (what happened when, what's coming up)
     * - Aggregation (spending totals, contact frequencies)
     * - Direct fact retrieval (medications, habits, preferences)
     */
    suspend fun reason(query: String, assembledContext: String): String? {
        val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US)

        // Schedule/time queries
        if (query.containsAny("schedule", "today", "calendar", "busy", "free", "plan")) {
            val now = System.currentTimeMillis()
            val endOfDay = now + 24 * 60 * 60 * 1000
            try {
                val events = extractedEventDao.getUpcomingEvents(now, endOfDay)
                return if (events.isNotEmpty()) {
                    val eventList = events.joinToString("\n") {
                        "- ${it.title} at ${dateFormat.format(Date(it.dateTimeMs))}" +
                            if (it.location.isNotBlank()) " (${it.location})" else ""
                    }
                    "Here's what's on your schedule:\n$eventList"
                } else {
                    "Your schedule looks clear for now. No upcoming events found."
                }
            } catch (e: Exception) { /* fall through */ }
        }

        // Medication queries
        if (query.containsAny("medication", "medicine", "pill", "dose", "refill")) {
            try {
                val meds = medicationDao.getActiveMedications()
                return if (meds.isNotEmpty()) {
                    val medList = meds.joinToString("\n") {
                        "- ${it.name} (${it.dosage}): ${it.frequency}. " +
                            "${it.pillsRemaining} pills remaining. Times: ${it.scheduledTimes}"
                    }
                    "Your current medications:\n$medList"
                } else {
                    "No active medications tracked."
                }
            } catch (e: Exception) { /* fall through */ }
        }

        // Contact/people queries
        if (query.containsAny("who", "contact", "call", "talk", "friend", "family")) {
            try {
                val contacts = contactContextDao.getTopByImportance(limit = 10)
                return if (contacts.isNotEmpty()) {
                    val contactList = contacts.joinToString("\n") {
                        "- ${it.contactName}: ${it.relationship}, ${it.totalCalls} calls, " +
                            "importance: ${String.format("%.1f", it.importance)}" +
                            if (it.birthday.isNotBlank()) ", birthday: ${it.birthday}" else ""
                    }
                    "Your key contacts:\n$contactList"
                } else {
                    "No contact history recorded yet."
                }
            } catch (e: Exception) { /* fall through */ }
        }

        // Spending queries
        if (query.containsAny("spend", "money", "budget", "cost", "finance", "transaction")) {
            try {
                val weekAgo = System.currentTimeMillis() - 7 * 24 * 60 * 60 * 1000
                val txs = transactionDao.getSince(sinceMs = weekAgo)
                return if (txs.isNotEmpty()) {
                    val total = txs.sumOf { it.amount }
                    val byCategory = txs.groupBy { it.category }
                        .mapValues { (_, t) -> t.sumOf { it.amount } }
                        .entries.sortedByDescending { it.value }
                        .joinToString("\n") {
                            "- ${it.key}: $${String.format("%.2f", it.value)}"
                        }
                    "This week's spending: $${String.format("%.2f", total)}\n$byCategory"
                } else {
                    "No transactions recorded this week."
                }
            } catch (e: Exception) { /* fall through */ }
        }

        // Habit queries
        if (query.containsAny("habit", "routine", "pattern", "usually")) {
            try {
                val habits = habitDao.getActivePatterns()
                return if (habits.isNotEmpty()) {
                    val habitList = habits.take(10).joinToString("\n") {
                        "- ${it.label}: ${it.description} (${it.patternType}, " +
                            "confidence: ${String.format("%.0f", it.confidence * 100)}%)"
                    }
                    "Your detected patterns:\n$habitList"
                } else {
                    "No patterns detected yet. I'm still learning your routines."
                }
            } catch (e: Exception) { /* fall through */ }
        }

        // Knowledge store search as last resort
        val facts = searchFacts(query, limit = 5)
        if (facts.isNotEmpty()) {
            return "Here's what I know:\n${facts.joinToString("\n") { "- $it" }}"
        }

        // If we have assembled context but no specific handler matched,
        // provide a summary of what we know
        if (assembledContext.isNotBlank() && assembledContext.length > 100) {
            return "[Processing locally — desktop offline]\n\n" +
                "I don't have a specific answer, but here's your current context:\n" +
                assembledContext.take(500)
        }

        return null
    }

    // ── Knowledge management ─────────────────────────────────────────────

    /**
     * Add a fact to the knowledge store.
     *
     * Facts are structured knowledge entries with category, confidence,
     * keywords, and source tracking. They persist across app restarts
     * and sync with the desktop.
     */
    fun addFact(
        content: String,
        category: String = CAT_GENERAL,
        confidence: Double = 0.8,
        keywords: List<String> = emptyList(),
        source: String = "phone",
    ) {
        synchronized(lock) {
            try {
                val facts = loadFacts()
                val fact = JSONObject().apply {
                    put("content", content)
                    put("category", category)
                    put("confidence", confidence)
                    put("keywords", keywords.joinToString(","))
                    put("source", source)
                    put("timestamp", System.currentTimeMillis())
                    put("synced", false)
                }
                facts.put(fact)

                // Enforce max capacity: remove oldest low-confidence facts
                val cleaned = enforceCapacity(facts)
                saveFacts(cleaned)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to add fact: ${e.message}")
            }
        }
    }

    /**
     * Import facts from the desktop's knowledge graph.
     *
     * Called during sync to update the phone's knowledge base with
     * the desktop's full knowledge. This is what makes the phone as
     * smart as the desktop — it gets the same knowledge, stored locally.
     */
    fun importDesktopFacts(facts: List<Map<String, Any>>) {
        synchronized(lock) {
            try {
                val existing = loadFacts()
                var added = 0

                for (factMap in facts) {
                    val content = factMap["content"]?.toString() ?: continue
                    // Don't add duplicates
                    var isDuplicate = false
                    for (i in 0 until existing.length()) {
                        if (existing.getJSONObject(i).optString("content") == content) {
                            isDuplicate = true
                            break
                        }
                    }
                    if (isDuplicate) continue

                    val fact = JSONObject().apply {
                        put("content", content)
                        put("category", factMap["category"]?.toString() ?: CAT_DESKTOP)
                        put("confidence", (factMap["confidence"] as? Number)?.toDouble() ?: 0.8)
                        put("keywords", factMap["keywords"]?.toString() ?: "")
                        put("source", "desktop")
                        put("timestamp", System.currentTimeMillis())
                        put("synced", true)
                    }
                    existing.put(fact)
                    added++
                }

                val cleaned = enforceCapacity(existing)
                saveFacts(cleaned)
                Log.i(TAG, "Imported $added facts from desktop")
            } catch (e: Exception) {
                Log.w(TAG, "Failed to import desktop facts: ${e.message}")
            }
        }
    }

    /**
     * Export phone-originated facts for syncing to desktop.
     *
     * Returns only facts that haven't been synced yet (source = "phone",
     * synced = false). After export, marks them as synced.
     */
    fun exportUnsyncedFacts(): List<Map<String, Any>> {
        synchronized(lock) {
            try {
                val facts = loadFacts()
                val unsynced = mutableListOf<Map<String, Any>>()

                for (i in 0 until facts.length()) {
                    val fact = facts.getJSONObject(i)
                    if (fact.optString("source") == "phone" && !fact.optBoolean("synced", false)) {
                        unsynced.add(mapOf(
                            "content" to fact.optString("content", ""),
                            "category" to fact.optString("category", CAT_GENERAL),
                            "confidence" to fact.optDouble("confidence", 0.8),
                            "keywords" to fact.optString("keywords", ""),
                            "source" to "phone",
                            "timestamp" to fact.optLong("timestamp", 0),
                        ))
                    }
                }

                return unsynced
            } catch (e: Exception) {
                Log.w(TAG, "Failed to export facts: ${e.message}")
                return emptyList()
            }
        }
    }

    /**
     * Mark exported facts as synced AFTER the network push succeeds.
     * Call this only after the desktop has acknowledged receipt.
     */
    fun markFactsSynced() {
        synchronized(lock) {
            try {
                val facts = loadFacts()
                var changed = false
                for (i in 0 until facts.length()) {
                    val fact = facts.getJSONObject(i)
                    if (fact.optString("source") == "phone" && !fact.optBoolean("synced", false)) {
                        fact.put("synced", true)
                        changed = true
                    }
                }
                if (changed) {
                    saveFacts(facts)
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to mark facts synced: ${e.message}")
            }
        }
    }

    /** Total number of facts in the store. */
    fun factCount(): Int {
        synchronized(lock) {
            return try { loadFacts().length() } catch (e: Exception) { 0 }
        }
    }

    // ── Internal ─────────────────────────────────────────────────────────

    private fun loadFacts(): JSONArray {
        return try {
            if (knowledgeFile.exists()) JSONArray(knowledgeFile.readText()) else JSONArray()
        } catch (e: Exception) { JSONArray() }
    }

    private fun saveFacts(facts: JSONArray) {
        knowledgeFile.writeText(facts.toString())
    }

    private fun enforceCapacity(facts: JSONArray): JSONArray {
        if (facts.length() <= MAX_FACTS) return facts

        // Convert to list, sort by (confidence * recency), keep top MAX_FACTS
        val now = System.currentTimeMillis()
        data class Entry(val obj: JSONObject, val score: Double)

        val entries = (0 until facts.length()).map { i ->
            val f = facts.getJSONObject(i)
            val confidence = f.optDouble("confidence", 0.5)
            val ageHours = (now - f.optLong("timestamp", 0)) / (1000.0 * 60 * 60)
            val recency = 1.0 / (1.0 + ageHours / 24.0) // Decay over days
            Entry(f, confidence * recency)
        }.sortedByDescending { it.score }
            .take(MAX_FACTS)

        val result = JSONArray()
        entries.forEach { result.put(it.obj) }
        return result
    }

    private fun String.containsAny(vararg words: String): Boolean =
        words.any { this.contains(it) }
}
