package com.jarvis.assistant.feature.finance

import android.app.NotificationManager
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.hilt.work.HiltWorker
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.jarvis.assistant.R
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Weekly spend summary worker that runs every Sunday via WorkManager.
 *
 * Queries the past 7 days of transactions, calculates totals and top
 * merchants, and posts a ROUTINE notification with the summary.
 */
@HiltWorker
class SpendSummaryWorker @AssistedInject constructor(
    @Assisted appContext: Context,
    @Assisted params: WorkerParameters,
    private val transactionDao: TransactionDao,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        return try {
            val dateFormat = SimpleDateFormat("yyyy-MM-dd", Locale.US)
            val cal = Calendar.getInstance()
            val endDate = dateFormat.format(cal.time)
            cal.add(Calendar.DAY_OF_YEAR, -7)
            val startDate = dateFormat.format(cal.time)

            val transactions = transactionDao.getTransactionsInRange(startDate, endDate)
            if (transactions.isEmpty()) {
                Log.d(TAG, "No transactions this week, skipping summary")
                return Result.success()
            }

            val totalSpend = transactionDao.getTotalSpendInRange(startDate, endDate) ?: 0.0
            val count = transactions.size
            val anomalyCount = transactions.count { it.isAnomaly }

            // Top 3 merchants by total spend
            val topMerchants = transactions
                .filter { it.category != "refund" }
                .groupBy { it.merchant }
                .mapValues { (_, txns) -> txns.sumOf { it.amount } }
                .entries
                .sortedByDescending { it.value }
                .take(3)

            val topMerchantStr = topMerchants.joinToString(", ") { (merchant, amount) ->
                "$merchant ($${formatAmount(amount)})"
            }

            val anomalyNote = if (anomalyCount > 0) {
                " $anomalyCount unusual transaction${if (anomalyCount > 1) "s" else ""} flagged."
            } else {
                ""
            }

            val body = "This week: $${formatAmount(totalSpend)} across $count transactions. " +
                "Top: $topMerchantStr.$anomalyNote"

            postNotification(body)
            Result.success()
        } catch (e: Exception) {
            Log.w(TAG, "Spend summary worker failed: ${e.message}")
            Result.retry()
        }
    }

    private fun postNotification(body: String) {
        val notification = NotificationCompat.Builder(
            applicationContext,
            NotificationPriority.ROUTINE.channelId,
        )
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Weekly Spend Summary")
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .build()

        val nm = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE)
            as NotificationManager
        nm.notify(NOTIFICATION_TAG, NOTIFICATION_ID, notification)
    }

    private fun formatAmount(amount: Double): String = "%.2f".format(amount)

    companion object {
        private const val TAG = "SpendSummaryWorker"
        private const val NOTIFICATION_TAG = "spend_summary"
        private const val NOTIFICATION_ID = 5003
        const val WORK_NAME = "spend_summary"

        /**
         * Enqueue the weekly spend summary worker.
         * Runs every 7 days with initial delay calculated to next Sunday 10:00 AM.
         */
        fun enqueue(context: Context) {
            val delay = calculateDelayToNextSunday()
            val request = PeriodicWorkRequestBuilder<SpendSummaryWorker>(7, TimeUnit.DAYS)
                .setInitialDelay(delay, TimeUnit.MILLISECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
            Log.i(TAG, "Spend summary worker enqueued, initial delay: ${delay / 3600_000}h")
        }

        /**
         * Calculate milliseconds until next Sunday at 10:00 AM.
         */
        fun calculateDelayToNextSunday(): Long {
            val now = Calendar.getInstance(Locale.US)
            val target = Calendar.getInstance(Locale.US).apply {
                set(Calendar.DAY_OF_WEEK, Calendar.SUNDAY)
                set(Calendar.HOUR_OF_DAY, 10)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
                // If already past this Sunday 10 AM, move to next week
                if (before(now)) {
                    add(Calendar.WEEK_OF_YEAR, 1)
                }
            }
            return target.timeInMillis - now.timeInMillis
        }
    }
}
