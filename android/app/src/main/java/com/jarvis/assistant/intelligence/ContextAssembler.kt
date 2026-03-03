package com.jarvis.assistant.intelligence

import android.util.Log
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.dao.CallLogDao
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Assembles rich contextual information from ALL local phone data.
 *
 * This is what makes the phone's intelligence real, not theoretical.
 * Instead of just searching a cache, the phone knows:
 * - What you're doing right now (context state)
 * - Who you've been talking to (contact memory)
 * - Where you usually go (commute patterns)
 * - What you spend money on (transaction history)
 * - What medications you take and when (health data)
 * - Your habits and routines (pattern detection)
 * - Your upcoming events (calendar)
 * - Your documents and notes (OCR/scanned docs)
 * - Your recent conversations (chat history)
 *
 * All of this gets fed to the AI as context, giving it deep personal
 * understanding equivalent to what the desktop has.
 */
@Singleton
class ContextAssembler @Inject constructor(
    private val conversationDao: ConversationDao,
    private val contactContextDao: ContactContextDao,
    private val contextStateDao: ContextStateDao,
    private val habitDao: HabitDao,
    private val commuteDao: CommuteDao,
    private val transactionDao: TransactionDao,
    private val extractedEventDao: ExtractedEventDao,
    private val medicationDao: MedicationDao,
    private val documentDao: DocumentDao,
    private val callLogDao: CallLogDao,
    private val knowledgeStore: LocalKnowledgeStore,
) {
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US)
    private val dayFormat = SimpleDateFormat("EEEE", Locale.US)

    /**
     * Build a comprehensive context string for the AI to reason over.
     *
     * Selectively includes data relevant to the query to stay within
     * token limits while maximizing usefulness.
     *
     * @param query The user's query (lowercased) to determine relevance
     * @return A formatted context string with all relevant local data
     */
    suspend fun assembleContext(query: String): String {
        val sections = mutableListOf<String>()
        val now = System.currentTimeMillis()
        val today = dateFormat.format(Date(now))
        val dayOfWeek = dayFormat.format(Date(now))

        // Always include: current time and context state
        sections.add("Current time: $today ($dayOfWeek)")

        // Current context (what are you doing right now?)
        try {
            val ctx = contextStateDao.getLatest()
            if (ctx != null) {
                sections.add("Current activity: ${ctx.context} (confidence: ${ctx.confidence})")
            }
        } catch (e: Exception) {
            Log.d(TAG, "Context state unavailable: ${e.message}")
        }

        // Recent conversation history (last 5 messages for continuity)
        try {
            val recent = conversationDao.getLatestMessages(limit = 5)
            if (recent.isNotEmpty()) {
                val convo = recent.joinToString("\n") { "  ${it.role}: ${it.content.take(200)}" }
                sections.add("Recent conversation:\n$convo")
            }
        } catch (e: Exception) {
            Log.d(TAG, "Conversation history unavailable: ${e.message}")
        }

        // Knowledge store facts relevant to query
        try {
            val facts = knowledgeStore.searchFacts(query, limit = 10)
            if (facts.isNotEmpty()) {
                sections.add("Relevant knowledge:\n${facts.joinToString("\n") { "  - $it" }}")
            }
        } catch (e: Exception) {
            Log.d(TAG, "Knowledge store unavailable: ${e.message}")
        }

        // Contacts and relationships (if query mentions people)
        if (query.containsAny("who", "call", "contact", "person", "friend", "family",
                "talk", "phone", "relationship", "birthday")) {
            try {
                val contacts = contactContextDao.getTopByImportance(limit = 10)
                if (contacts.isNotEmpty()) {
                    val contactInfo = contacts.joinToString("\n") {
                        "  - ${it.contactName}: ${it.relationship}, ${it.totalCalls} calls, " +
                            "importance: ${it.importance}, last: ${it.lastCallDate}" +
                            if (it.birthday.isNotBlank()) ", birthday: ${it.birthday}" else ""
                    }
                    sections.add("Important contacts:\n$contactInfo")
                }
            } catch (e: Exception) {
                Log.d(TAG, "Contact data unavailable: ${e.message}")
            }
        }

        // Upcoming events (if query is about schedule/calendar/today)
        if (query.containsAny("schedule", "calendar", "event", "meeting", "today",
                "tomorrow", "week", "plan", "busy", "free", "available", "when")) {
            try {
                val upcoming = extractedEventDao.getUpcomingEvents(
                    fromMs = now, toMs = now + 7 * 24 * 60 * 60 * 1000,
                )
                if (upcoming.isNotEmpty()) {
                    val events = upcoming.take(10).joinToString("\n") {
                        "  - ${it.title} at ${dateFormat.format(Date(it.dateTimeMs))}" +
                            if (it.location.isNotBlank()) " (${it.location})" else ""
                    }
                    sections.add("Upcoming events:\n$events")
                }
            } catch (e: Exception) {
                Log.d(TAG, "Event data unavailable: ${e.message}")
            }
        }

        // Medications (if query mentions health/meds/pills)
        if (query.containsAny("medication", "medicine", "pill", "dose", "prescription",
                "refill", "health", "take", "drug")) {
            try {
                val meds = medicationDao.getActiveMedications()
                if (meds.isNotEmpty()) {
                    val medInfo = meds.joinToString("\n") {
                        "  - ${it.name} ${it.dosage}: ${it.frequency}, " +
                            "${it.pillsRemaining} pills remaining, " +
                            "times: ${it.scheduledTimes}"
                    }
                    sections.add("Active medications:\n$medInfo")
                }
            } catch (e: Exception) {
                Log.d(TAG, "Medication data unavailable: ${e.message}")
            }
        }

        // Spending/finance (if query mentions money)
        if (query.containsAny("spend", "money", "budget", "transaction", "cost",
                "buy", "purchase", "payment", "bill", "finance", "expensive")) {
            try {
                val weekAgo = now - 7 * 24 * 60 * 60 * 1000
                val recentTx = transactionDao.getSince(sinceMs = weekAgo)
                if (recentTx.isNotEmpty()) {
                    val total = recentTx.sumOf { it.amount }
                    val byCategory = recentTx.groupBy { it.category }
                        .mapValues { (_, txs) -> txs.sumOf { it.amount } }
                        .entries.sortedByDescending { it.value }
                        .take(5)
                        .joinToString(", ") { "${it.key}: $${String.format("%.2f", it.value)}" }
                    sections.add("Spending this week: $${String.format("%.2f", total)} total ($byCategory)")
                    if (recentTx.any { it.isAnomaly }) {
                        val anomalies = recentTx.filter { it.isAnomaly }.joinToString(", ") {
                            "${it.merchant} ($${it.amount})"
                        }
                        sections.add("Spending anomalies: $anomalies")
                    }
                }
            } catch (e: Exception) {
                Log.d(TAG, "Transaction data unavailable: ${e.message}")
            }
        }

        // Habits and patterns
        if (query.containsAny("habit", "routine", "pattern", "usually", "always",
                "every", "typical", "normal")) {
            try {
                val habits = habitDao.getActivePatterns()
                if (habits.isNotEmpty()) {
                    val habitInfo = habits.take(10).joinToString("\n") {
                        "  - ${it.label}: ${it.description} " +
                            "(${it.patternType}, confidence: ${it.confidence})"
                    }
                    sections.add("Detected habits/patterns:\n$habitInfo")
                }
            } catch (e: Exception) {
                Log.d(TAG, "Habit data unavailable: ${e.message}")
            }
        }

        // Locations and commute
        if (query.containsAny("where", "location", "commute", "drive", "go",
                "place", "visit", "nearby", "home", "work", "gym")) {
            try {
                val locations = commuteDao.getMostVisited(limit = 10)
                if (locations.isNotEmpty()) {
                    val locInfo = locations.joinToString("\n") {
                        "  - ${it.label}: visited ${it.visitCount} times, " +
                            "avg arrival: ${String.format("%.0f", it.avgArrivalHour)}:00"
                    }
                    sections.add("Known locations:\n$locInfo")
                }
            } catch (e: Exception) {
                Log.d(TAG, "Location data unavailable: ${e.message}")
            }
        }

        // Documents (if query mentions documents/notes/scan)
        if (query.containsAny("document", "scan", "note", "receipt", "paper",
                "file", "pdf", "text", "read", "ocr")) {
            try {
                val docs = documentDao.getRecentDocuments(limit = 5)
                if (docs.isNotEmpty()) {
                    val docInfo = docs.joinToString("\n") {
                        "  - ${it.title} (${it.category}): ${it.ocrText.take(100)}..."
                    }
                    sections.add("Recent documents:\n$docInfo")
                }
            } catch (e: Exception) {
                Log.d(TAG, "Document data unavailable: ${e.message}")
            }
        }

        return sections.joinToString("\n\n")
    }

    private fun String.containsAny(vararg words: String): Boolean =
        words.any { this.contains(it) }

    companion object {
        private const val TAG = "ContextAssembler"
    }
}
