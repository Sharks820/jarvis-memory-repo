package com.jarvis.assistant.feature.finance

import android.util.Log
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.TransactionEntity
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Parsed transaction data extracted from a bank notification.
 */
data class ParsedTransaction(
    val amount: Double,
    val merchant: String,
    val category: String,
    val rawText: String,
)

/**
 * Parses bank SMS/email notifications into [TransactionEntity] records.
 *
 * Uses regex patterns for major US bank notification formats (Chase, Bank of
 * America, Wells Fargo) with a generic fallback for amount extraction. After
 * parsing, each transaction is checked for anomalies by [AnomalyDetector] and
 * stored in the Room database via [TransactionDao].
 */
@Singleton
class BankNotificationParser @Inject constructor(
    private val transactionDao: TransactionDao,
    private val anomalyDetector: AnomalyDetector,
) {

    /**
     * Attempt to parse a bank notification and store as a transaction.
     *
     * @param packageName Android package name of the notification source.
     * @param notificationText Full notification body text.
     * @return The stored [TransactionEntity], or null if the text could not be parsed.
     */
    suspend fun parseAndStore(packageName: String, notificationText: String): TransactionEntity? {
        val parsed = parse(notificationText) ?: return null
        val hash = sha256(parsed.rawText)
        val dateFormat = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        val today = dateFormat.format(Date())

        var entity = TransactionEntity(
            amount = parsed.amount,
            merchant = parsed.merchant,
            category = parsed.category,
            sourceApp = packageName,
            rawText = parsed.rawText,
            date = today,
            notificationHash = hash,
        )

        val insertId = transactionDao.insert(entity)
        if (insertId < 0) {
            Log.d(TAG, "Duplicate notification skipped: $hash")
            return null
        }

        entity = entity.copy(id = insertId)

        // Check for anomalies
        try {
            val anomalyResult = anomalyDetector.check(entity)
            if (anomalyResult.isAnomaly) {
                entity = entity.copy(
                    isAnomaly = true,
                    anomalyReason = anomalyResult.reason,
                )
                transactionDao.update(entity)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Anomaly check failed: ${e.message}")
        }

        return entity
    }

    /**
     * Check if a package name belongs to a known bank app.
     */
    fun isBankApp(packageName: String): Boolean {
        if (packageName in BANK_PACKAGES) return true
        val lower = packageName.lowercase()
        return lower.contains("bank") ||
            lower.contains("finance") ||
            lower.contains("credit")
    }

    // ── Internal Parsing ──────────────────────────────────────────────

    private fun parse(text: String): ParsedTransaction? {
        val amount = extractAmount(text) ?: return null
        val merchant = extractMerchant(text) ?: "Unknown Merchant"
        val category = classifyCategory(text, merchant)

        return ParsedTransaction(
            amount = amount,
            merchant = merchant.trim(),
            category = category,
            rawText = text,
        )
    }

    private fun extractAmount(text: String): Double? {
        // Try bank-specific patterns first, then fall back to generic
        for (pattern in AMOUNT_PATTERNS) {
            val match = pattern.find(text)
            if (match != null) {
                val amountStr = match.groupValues[1].replace(",", "")
                return amountStr.toDoubleOrNull()
            }
        }
        return null
    }

    private fun extractMerchant(text: String): String? {
        for (pattern in MERCHANT_PATTERNS) {
            val match = pattern.find(text)
            if (match != null) {
                return match.groupValues[1].trim()
                    .removeSuffix(".")
                    .take(100) // cap length
            }
        }
        return null
    }

    private fun classifyCategory(text: String, merchant: String): String {
        val lower = text.lowercase()
        val merchantLower = merchant.lowercase()

        return when {
            SUBSCRIPTION_MERCHANTS.any { merchantLower.contains(it) } -> "subscription"
            lower.contains("atm") || lower.contains("cash") -> "atm"
            lower.contains("transfer") || lower.contains("zelle") ||
                lower.contains("venmo") -> "transfer"
            lower.contains("refund") || lower.contains("credit") -> "refund"
            lower.contains("fee") || lower.contains("overdraft") -> "fee"
            else -> "purchase"
        }
    }

    private fun sha256(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val hash = digest.digest(input.toByteArray(Charsets.UTF_8))
        return hash.joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val TAG = "BankNotifParser"

        /** Known bank app Android package names. */
        private val BANK_PACKAGES = setOf(
            "com.chase.sig.android",
            "com.infonow.bofa",
            "com.wf.wellsfargomobile",
            "com.usaa.mobile.android.usaa",
            "com.citi.citimobile",
            "com.ally.MobileBanking",
            "com.capitalone.mobile",
        )

        /** Subscription services for category classification. */
        private val SUBSCRIPTION_MERCHANTS = listOf(
            "netflix", "spotify", "disney+", "hulu", "hbo",
            "apple music", "youtube premium", "amazon prime",
            "paramount+", "peacock", "crunchyroll",
        )

        /** Amount extraction regex patterns ordered by bank specificity. */
        private val AMOUNT_PATTERNS = listOf(
            // Chase: "purchase/charge/transaction ... $X.XX ... at Merchant"
            Regex("""(?i)(?:purchase|charge|transaction).*?\$([\d,]+\.\d{2})"""),
            // Bank of America: "charge/debit ... $X.XX ... at/from Merchant"
            Regex("""(?i)(?:charge|debit).*?\$([\d,]+\.\d{2})"""),
            // Wells Fargo: "debit card/purchase ... $X.XX ... at/for Merchant"
            Regex("""(?i)(?:debit card|purchase).*?\$([\d,]+\.\d{2})"""),
            // Generic fallback: any dollar amount
            Regex("""\$([\d,]+\.\d{2})"""),
        )

        /** Merchant extraction regex patterns. */
        private val MERCHANT_PATTERNS = listOf(
            // "at Merchant" pattern
            Regex("""(?i)(?:at|from|for)\s+(.+?)(?:\.|$|on\s+\d)"""),
        )
    }
}
