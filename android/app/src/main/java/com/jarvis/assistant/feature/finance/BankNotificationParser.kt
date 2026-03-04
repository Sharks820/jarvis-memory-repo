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
    val direction: String = "debit",
    val counterparty: String = "",
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
    private val merchantNormalizer: MerchantNormalizer,
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

        val normalized = merchantNormalizer.normalize(parsed.merchant)

        var entity = TransactionEntity(
            amount = parsed.amount,
            merchant = parsed.merchant,
            normalizedMerchant = normalized,
            category = parsed.category,
            sourceApp = packageName,
            rawText = parsed.rawText,
            direction = parsed.direction,
            counterparty = parsed.counterparty,
            date = today,
            notificationHash = hash,
        )

        // Check for anomalies BEFORE insert so DB averages aren't diluted
        try {
            val anomalyResult = anomalyDetector.check(entity)
            if (anomalyResult.isAnomaly) {
                entity = entity.copy(
                    isAnomaly = true,
                    anomalyReason = anomalyResult.reason,
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "Anomaly check failed: ${e.message}")
        }

        val insertId = transactionDao.insert(entity)
        if (insertId < 0) {
            Log.d(TAG, "Duplicate notification skipped: $hash")
            return null
        }

        return entity.copy(id = insertId)
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
        // Try P2P received patterns first
        for (pattern in P2P_RECEIVED_PATTERNS) {
            val match = pattern.find(text) ?: continue
            val groups = match.groupValues
            // Some patterns have counterparty in group 1, amount in group 2;
            // "received $X from Y" has amount in group 1, counterparty in group 2
            val amount: Double
            val counterparty: String
            if (groups[1].startsWith("$") || groups[1].all { it.isDigit() || it == ',' || it == '.' }) {
                amount = groups[1].replace(",", "").toDoubleOrNull() ?: continue
                counterparty = groups[2].trim()
            } else {
                counterparty = groups[1].trim()
                amount = groups[2].replace(",", "").toDoubleOrNull() ?: continue
            }
            return ParsedTransaction(
                amount = amount,
                merchant = counterparty,
                category = "transfer",
                rawText = text,
                direction = "credit",
                counterparty = counterparty,
            )
        }

        // Try P2P sent patterns
        for (pattern in P2P_SENT_PATTERNS) {
            val match = pattern.find(text) ?: continue
            val groups = match.groupValues
            val amount: Double
            val counterparty: String
            if (groups[1].startsWith("$") || groups[1].all { it.isDigit() || it == ',' || it == '.' }) {
                amount = groups[1].replace(",", "").toDoubleOrNull() ?: continue
                counterparty = groups[2].trim()
            } else {
                counterparty = groups[1].trim()
                amount = groups[2].replace(",", "").toDoubleOrNull() ?: continue
            }
            return ParsedTransaction(
                amount = amount,
                merchant = counterparty,
                category = "transfer",
                rawText = text,
                direction = "debit",
                counterparty = counterparty,
            )
        }

        // Try income/deposit patterns
        for (pattern in INCOME_PATTERNS) {
            val match = pattern.find(text) ?: continue
            val amount = match.groupValues[1].replace(",", "").toDoubleOrNull() ?: continue
            return ParsedTransaction(
                amount = amount,
                merchant = extractMerchant(text) ?: "Direct Deposit",
                category = "transfer",
                rawText = text,
                direction = "credit",
            )
        }

        // Standard bank notification parsing
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

        /** Known bank and P2P app Android package names. */
        private val BANK_PACKAGES = setOf(
            "com.chase.sig.android",
            "com.infonow.bofa",
            "com.wf.wellsfargomobile",
            "com.usaa.mobile.android.usaa",
            "com.citi.citimobile",
            "com.ally.MobileBanking",
            "com.capitalone.mobile",
            // P2P payment apps
            "com.venmo",
            "com.paypal.android.p2pmobile",
            "com.squareup.cash",
            "com.google.android.apps.nbu.paisa.user",
            "com.zellepay.zelle",
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

        /** P2P received (credit) patterns — counterparty in group 1, amount in group 2. */
        private val P2P_RECEIVED_PATTERNS = listOf(
            Regex("""(?i)(\w[\w\s]*?)\s+paid\s+you\s+\$([\d,]+\.\d{2})"""),
            Regex("""(?i)received\s+\$([\d,]+\.\d{2})\s+from\s+(.+?)(?:\.|$)"""),
            Regex("""(?i)(\w[\w\s]*?)\s+sent\s+you\s+\$([\d,]+\.\d{2})"""),
        )

        /** P2P sent (debit) patterns — counterparty in group 2 or 1, amount in other group. */
        private val P2P_SENT_PATTERNS = listOf(
            Regex("""(?i)you\s+paid\s+(.+?)\s+\$([\d,]+\.\d{2})"""),
            Regex("""(?i)you\s+sent\s+\$([\d,]+\.\d{2})\s+to\s+(.+?)(?:\.|$)"""),
        )

        /** Income/deposit detection patterns — amount in group 1. */
        private val INCOME_PATTERNS = listOf(
            Regex("""(?i)(?:direct\s+)?deposit\s+(?:of\s+)?\$([\d,]+\.\d{2})"""),
            Regex("""(?i)payroll\s+(?:of\s+)?\$([\d,]+\.\d{2})"""),
        )
    }
}
