package com.jarvis.assistant.feature.finance

import android.util.Log
import com.jarvis.assistant.data.dao.RecurringPatternDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.RecurringPatternEntity
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.abs

/**
 * Detects pay schedule from credit transaction patterns.
 *
 * Groups credit transactions > $100 by approximate amount (±5%), then
 * computes inter-transaction intervals to classify the period
 * (weekly / biweekly / monthly / quarterly / annual). Results are
 * stored in [RecurringPatternEntity] for downstream features.
 */
@Singleton
class IncomeCycleDetector @Inject constructor(
    private val transactionDao: TransactionDao,
    private val recurringPatternDao: RecurringPatternDao,
) {
    companion object {
        private const val TAG = "IncomeCycleDetector"
        private const val AMOUNT_TOLERANCE = 0.05
        private const val MIN_OCCURRENCES = 2
    }

    suspend fun detectCycles() {
        val fmt = DateTimeFormatter.ofPattern("yyyy-MM-dd")
        val since = LocalDate.now().minusDays(90).format(fmt)
        val today = LocalDate.now().format(fmt)

        val credits = transactionDao.getTransactionsInRange(since, today)
            .filter { it.direction == "credit" && it.amount > 100.0 }

        if (credits.isEmpty()) return

        // Group by approximate amount (±5%)
        val groups = mutableListOf<MutableList<Pair<String, Double>>>()
        for (tx in credits) {
            val matched = groups.find { group ->
                group.any { (_, amt) -> abs(tx.amount - amt) / amt < AMOUNT_TOLERANCE }
            }
            if (matched != null) {
                matched.add(tx.date to tx.amount)
            } else {
                groups.add(mutableListOf(tx.date to tx.amount))
            }
        }

        // For groups with 2+ occurrences, detect period
        for (group in groups.filter { it.size >= MIN_OCCURRENCES }) {
            try {
                val dates = group.map { (d, _) -> LocalDate.parse(d, fmt) }.sorted()
                val intervals = dates.zipWithNext { a, b -> ChronoUnit.DAYS.between(a, b) }
                if (intervals.isEmpty()) continue
                val medianInterval = intervals.sorted()[intervals.size / 2]
                val period = classifyPeriod(medianInterval) ?: continue
                val avgAmount = group.map { it.second }.average()

                val existing = recurringPatternDao.findByMerchant(
                    merchant = "Income",
                    direction = "credit",
                )
                val entity = RecurringPatternEntity(
                    id = existing?.id ?: 0,
                    merchant = "Income",
                    normalizedAmount = avgAmount,
                    period = period,
                    direction = "credit",
                    lastSeen = dates.last().format(fmt),
                    firstSeen = dates.first().format(fmt),
                    isActive = true,
                    occurrenceCount = group.size,
                )
                recurringPatternDao.upsert(entity)
                Log.i(TAG, "Detected income cycle: $period ~$${String.format("%.2f", avgAmount)}")
            } catch (e: Exception) {
                Log.w(TAG, "Error processing group: ${e.message}")
            }
        }
    }

    private fun classifyPeriod(days: Long): String? = when (days) {
        in 5..9 -> "WEEKLY"
        in 12..16 -> "BIWEEKLY"
        in 27..35 -> "MONTHLY"
        in 85..100 -> "QUARTERLY"
        in 355..375 -> "ANNUAL"
        else -> null
    }
}
