package com.jarvis.assistant.feature.finance

import android.app.NotificationManager
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.TransactionEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Result of an anomaly check on a transaction.
 */
data class AnomalyResult(
    val isAnomaly: Boolean,
    val reason: String,
)

/**
 * Detects anomalous financial transactions by comparing against historical
 * patterns stored in the Room database.
 *
 * Three anomaly types are checked:
 * 1. **Unusual amount** -- transaction > 3x the average for this category over 90 days
 * 2. **New merchant** -- first transaction at this merchant and amount > $50
 * 3. **Subscription price change** -- subscription amount differs > 10% from average
 *
 * When an anomaly is detected, an IMPORTANT-priority notification is posted.
 */
@Singleton
class AnomalyDetector @Inject constructor(
    @ApplicationContext private val context: Context,
    private val transactionDao: TransactionDao,
) {

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Check a transaction for anomalies against historical data.
     *
     * @param transaction The transaction to evaluate.
     * @return [AnomalyResult] indicating whether an anomaly was found and why.
     */
    suspend fun check(transaction: TransactionEntity): AnomalyResult {
        // Respect per-check toggle settings
        val jarvisPrefs = context.getSharedPreferences("jarvis_prefs", Context.MODE_PRIVATE)

        // Check in priority order: subscription price change, unusual amount, new merchant
        checkSubscriptionPriceChange(transaction)?.let { return it }
        if (jarvisPrefs.getBoolean("alert_unusual_amounts", true)) {
            checkUnusualAmount(transaction)?.let { return it }
        }
        if (jarvisPrefs.getBoolean("alert_new_merchants", true)) {
            checkNewMerchant(transaction)?.let { return it }
        }

        return AnomalyResult(isAnomaly = false, reason = "")
    }

    // ── Anomaly Checks ────────────────────────────────────────────────

    /**
     * Flag if transaction amount > 3x the 90-day average for this category.
     */
    private suspend fun checkUnusualAmount(transaction: TransactionEntity): AnomalyResult? {
        val dateFormat = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        val cal = Calendar.getInstance()
        cal.add(Calendar.DAY_OF_YEAR, -90)
        val sinceDate = dateFormat.format(cal.time)

        val avgAmount = transactionDao.getAverageAmountForCategory(
            transaction.category,
            sinceDate,
        ) ?: return null

        if (avgAmount <= 0) return null

        val multiplier = transaction.amount / avgAmount
        if (multiplier > 3.0) {
            val reason = "Unusual charge: $${formatAmount(transaction.amount)} " +
                "is ${"%.1f".format(multiplier)}x your average " +
                "${transaction.category} spend"
            postAlert(reason)
            return AnomalyResult(isAnomaly = true, reason = reason)
        }
        return null
    }

    /**
     * Flag first-time merchants with charges over $50.
     */
    private suspend fun checkNewMerchant(transaction: TransactionEntity): AnomalyResult? {
        val stats = transactionDao.getMerchantStats(transaction.merchant)
        // Anomaly check runs BEFORE insert (so DB averages aren't diluted).
        // A "new" merchant means zero prior transactions in the DB.
        val isNew = stats == null || stats.count < 1
        if (isNew && transaction.amount > NEW_MERCHANT_THRESHOLD) {
            val reason = "First transaction at ${transaction.merchant} " +
                "for $${formatAmount(transaction.amount)}"
            postAlert(reason)
            return AnomalyResult(isAnomaly = true, reason = reason)
        }
        return null
    }

    /**
     * Flag subscription price changes > 10%.
     */
    private suspend fun checkSubscriptionPriceChange(
        transaction: TransactionEntity,
    ): AnomalyResult? {
        if (transaction.category != "subscription") return null

        val stats = transactionDao.getMerchantStats(transaction.merchant) ?: return null
        if (stats.count <= 1) return null // Need history to detect change

        val priceDelta = kotlin.math.abs(transaction.amount - stats.avgAmount) / stats.avgAmount
        if (priceDelta > SUBSCRIPTION_PRICE_CHANGE_THRESHOLD) {
            val reason = "Subscription price change at ${transaction.merchant}: " +
                "was $${formatAmount(stats.avgAmount)}, " +
                "now $${formatAmount(transaction.amount)}"
            postAlert(reason)
            return AnomalyResult(isAnomaly = true, reason = reason)
        }
        return null
    }

    // ── Notification ──────────────────────────────────────────────────

    private fun postAlert(reason: String) {
        try {
            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.IMPORTANT.channelId,
            )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Financial Alert")
                .setContentText(reason)
                .setStyle(NotificationCompat.BigTextStyle().bigText(reason))
                .setAutoCancel(true)
                .build()

            notificationManager.notify(
                NOTIFICATION_TAG,
                System.currentTimeMillis().rem(Int.MAX_VALUE).toInt(),
                notification,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to post financial alert: ${e.message}")
        }
    }

    private fun formatAmount(amount: Double): String = "%.2f".format(amount)

    companion object {
        private const val TAG = "AnomalyDetector"
        private const val NOTIFICATION_TAG = "financial_alert"

        /** Only flag new merchants if charge exceeds this amount. */
        private const val NEW_MERCHANT_THRESHOLD = 50.0

        /** Subscription price change threshold (10%). */
        private const val SUBSCRIPTION_PRICE_CHANGE_THRESHOLD = 0.10
    }
}
