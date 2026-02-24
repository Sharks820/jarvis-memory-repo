package com.jarvis.assistant.feature.notifications

import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.entity.NotificationLogEntity
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks user act-vs-dismiss patterns on Jarvis notifications and adjusts
 * notification priority over time.
 *
 * Uses a rolling 30-day window. If the dismiss rate for an alert type exceeds
 * 80% (with at least 5 samples), the priority is demoted one level. If the
 * act rate exceeds 80%, the priority is promoted one level.
 */
@Singleton
class NotificationLearner @Inject constructor(
    private val notificationLogDao: NotificationLogDao,
) {

    /**
     * Log a user interaction with a Jarvis notification.
     *
     * @param action One of "acted", "dismissed", "expired".
     * @param actionDelayMs Time in milliseconds between notification post and user action.
     */
    suspend fun logAction(
        notificationId: Int,
        alertType: String,
        title: String,
        channelId: String,
        action: String,
        actionDelayMs: Long,
    ) {
        notificationLogDao.insert(
            NotificationLogEntity(
                notificationId = notificationId,
                alertType = alertType,
                title = title,
                channelId = channelId,
                action = action,
                actionDelayMs = actionDelayMs,
            ),
        )
    }

    /**
     * Get the adjusted priority for an alert type based on historical user behaviour.
     *
     * - Dismiss rate > 80% (min 5 samples) --> demote one level
     * - Act rate > 80% (min 5 samples) --> promote one level
     * - Otherwise --> keep base priority
     */
    suspend fun getAdjustedPriority(
        alertType: String,
        basePriority: NotificationPriority,
    ): NotificationPriority {
        val since = System.currentTimeMillis() - WINDOW_MS

        val counts = notificationLogDao.getActionCounts(since)
        val typeCounts = counts.filter { it.alertType == alertType }

        val acted = typeCounts.filter { it.action == "acted" }.sumOf { it.cnt }
        val dismissed = typeCounts.filter { it.action == "dismissed" }.sumOf { it.cnt }
        val total = acted + dismissed

        if (total < MIN_SAMPLE_SIZE) return basePriority

        val dismissRate = dismissed.toFloat() / total
        val actRate = acted.toFloat() / total

        return when {
            dismissRate > THRESHOLD -> demote(basePriority)
            actRate > THRESHOLD -> promote(basePriority)
            else -> basePriority
        }
    }

    /**
     * Get the dismiss rate for each alert type over the last 30 days.
     * Used by Settings UI to show learning insights.
     *
     * @return Map of alertType to dismiss rate (0.0 - 1.0).
     */
    suspend fun getLearningSummary(): Map<String, Float> {
        val since = System.currentTimeMillis() - WINDOW_MS
        val counts = notificationLogDao.getActionCounts(since)

        val result = mutableMapOf<String, Float>()
        val grouped = counts.groupBy { it.alertType }

        for ((alertType, actionCounts) in grouped) {
            val acted = actionCounts.filter { it.action == "acted" }.sumOf { it.cnt }
            val dismissed = actionCounts.filter { it.action == "dismissed" }.sumOf { it.cnt }
            val total = acted + dismissed
            if (total > 0) {
                result[alertType] = dismissed.toFloat() / total
            }
        }

        return result
    }

    /** Reset all learning data. */
    suspend fun resetLearningData() {
        notificationLogDao.deleteAll()
    }

    private fun promote(priority: NotificationPriority): NotificationPriority {
        return when (priority) {
            NotificationPriority.BACKGROUND -> NotificationPriority.ROUTINE
            NotificationPriority.ROUTINE -> NotificationPriority.IMPORTANT
            NotificationPriority.IMPORTANT -> NotificationPriority.URGENT
            NotificationPriority.URGENT -> NotificationPriority.URGENT // already highest
        }
    }

    private fun demote(priority: NotificationPriority): NotificationPriority {
        return when (priority) {
            NotificationPriority.URGENT -> NotificationPriority.IMPORTANT
            NotificationPriority.IMPORTANT -> NotificationPriority.ROUTINE
            NotificationPriority.ROUTINE -> NotificationPriority.BACKGROUND
            NotificationPriority.BACKGROUND -> NotificationPriority.BACKGROUND // already lowest
        }
    }

    companion object {
        /** 30-day rolling window for learning. */
        private const val WINDOW_MS = 30L * 24 * 60 * 60 * 1000

        /** Minimum number of acted+dismissed events before adjusting priority. */
        private const val MIN_SAMPLE_SIZE = 5

        /** Act or dismiss rate threshold to trigger priority change. */
        private const val THRESHOLD = 0.80f
    }
}
