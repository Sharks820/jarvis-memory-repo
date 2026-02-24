package com.jarvis.assistant.feature.documents

import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import java.util.Calendar
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Natural language document search: parses queries to extract content terms,
 * category hints, and date filters, then delegates to Room DAO LIKE search.
 *
 * Example: "find my Best Buy receipt from January" ->
 *   search terms = "Best Buy", category = "receipt", date = January of current year.
 */
@Singleton
class DocumentSearchEngine @Inject constructor(
    private val documentDao: DocumentDao,
) {

    /** Month names mapped to Calendar month constants (0-based). */
    private val monthMap = mapOf(
        "january" to Calendar.JANUARY, "february" to Calendar.FEBRUARY,
        "march" to Calendar.MARCH, "april" to Calendar.APRIL,
        "may" to Calendar.MAY, "june" to Calendar.JUNE,
        "july" to Calendar.JULY, "august" to Calendar.AUGUST,
        "september" to Calendar.SEPTEMBER, "october" to Calendar.OCTOBER,
        "november" to Calendar.NOVEMBER, "december" to Calendar.DECEMBER,
    )

    /** Category keywords that map to document categories. */
    private val categoryHints = mapOf(
        "receipt" to DocumentCategorizer.CATEGORY_RECEIPT,
        "receipts" to DocumentCategorizer.CATEGORY_RECEIPT,
        "warranty" to DocumentCategorizer.CATEGORY_WARRANTY,
        "warranties" to DocumentCategorizer.CATEGORY_WARRANTY,
        "id" to DocumentCategorizer.CATEGORY_ID,
        "identification" to DocumentCategorizer.CATEGORY_ID,
        "license" to DocumentCategorizer.CATEGORY_ID,
        "medical" to DocumentCategorizer.CATEGORY_MEDICAL,
        "prescription" to DocumentCategorizer.CATEGORY_MEDICAL,
        "insurance" to DocumentCategorizer.CATEGORY_INSURANCE,
    )

    /** Words to strip from the query before extracting content terms. */
    private val stopWords = setOf(
        "find", "search", "show", "get", "where", "is", "look", "for",
        "my", "me", "the", "a", "an", "from", "in", "of", "with",
        "document", "documents", "scan", "scans", "paper", "papers",
    )

    /**
     * Parse a natural language query and search documents.
     *
     * @param query  Natural language search text.
     * @param category  Optional explicit category override.
     * @return List of matching documents, most recent first.
     */
    suspend fun search(
        query: String,
        category: String? = null,
    ): List<ScannedDocumentEntity> {
        val words = query.lowercase().split("\\s+".toRegex())

        // 1. Extract date hints
        var monthFilter: Int? = null
        var yearFilter: Int? = null
        for (word in words) {
            monthMap[word]?.let { monthFilter = it }
            val yearMatch = YEAR_REGEX.matchEntire(word)
            if (yearMatch != null) {
                yearFilter = yearMatch.value.toInt()
            }
        }

        // 2. Extract category hints (query can override the explicit parameter)
        val detectedCategory = category ?: words.firstNotNullOfOrNull { categoryHints[it] }

        // 3. Build content search terms: remove stop words, date words, category words
        val dateAndCategoryWords = monthMap.keys + categoryHints.keys + stopWords
        val searchTerms = words
            .filter { it !in dateAndCategoryWords && !YEAR_REGEX.matches(it) }
            .joinToString(" ")
            .trim()

        // 4. Execute DAO search
        val results = if (searchTerms.isBlank()) {
            // No content terms -- return by category or all
            if (detectedCategory != null) {
                documentDao.searchByContentAndCategory("", detectedCategory)
            } else {
                documentDao.searchByContent("")
            }
        } else if (detectedCategory != null) {
            documentDao.searchByContentAndCategory(searchTerms, detectedCategory)
        } else {
            documentDao.searchByContent(searchTerms)
        }

        // 5. Post-filter by date if date hints were found
        val filtered = if (monthFilter != null || yearFilter != null) {
            results.filter { doc ->
                val cal = Calendar.getInstance().apply { timeInMillis = doc.createdAt }
                val monthMatch = monthFilter == null || cal.get(Calendar.MONTH) == monthFilter
                val yearMatch = yearFilter == null || cal.get(Calendar.YEAR) == yearFilter
                // If only month specified without year, assume current year
                if (yearFilter == null && monthFilter != null) {
                    val currentYear = Calendar.getInstance().get(Calendar.YEAR)
                    monthMatch && cal.get(Calendar.YEAR) == currentYear
                } else {
                    monthMatch && yearMatch
                }
            }
        } else {
            results
        }

        return filtered.sortedByDescending { it.createdAt }
    }

    companion object {
        private val YEAR_REGEX = Regex("20\\d{2}")
    }
}
