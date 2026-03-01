package com.jarvis.assistant.feature.documents

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Handles voice queries about scanned documents.
 *
 * Matches patterns like "find my receipt", "search for my warranty",
 * "where is my Best Buy receipt from January", etc.
 */
@Singleton
class DocumentVoiceHandler @Inject constructor(
    private val searchEngine: DocumentSearchEngine,
) {

    private val documentPattern = Regex(
        "(?i)(find|search|show|get|where is|look for).*" +
            "(?:document|receipt|warranty|id|medical|insurance|scan|paper)",
    )

    /** Returns true if the query text matches document search patterns. */
    fun matchesDocumentQuery(query: String): Boolean =
        documentPattern.containsMatchIn(query)

    /**
     * Handle a voice query about documents.
     *
     * @return A natural language response string, or null if the query
     *         doesn't match document patterns.
     */
    suspend fun handleQuery(query: String): String? {
        if (!matchesDocumentQuery(query)) return null

        val results = searchEngine.search(query)
        // Create locally to avoid thread-safety issues — SimpleDateFormat is not thread-safe
        // and this @Singleton's handleQuery() can be called concurrently.
        val dateFormat = SimpleDateFormat("MMM d, yyyy", Locale.US)

        return if (results.isNotEmpty()) {
            val most = results.first()
            val dateStr = dateFormat.format(Date(most.createdAt))
            if (results.size == 1) {
                "I found 1 document. It's '${most.title}' (${most.category}) from $dateStr."
            } else {
                "I found ${results.size} document(s). The most recent is '${most.title}' " +
                    "(${most.category}) from $dateStr."
            }
        } else {
            "I couldn't find any documents matching that query, sir."
        }
    }
}
