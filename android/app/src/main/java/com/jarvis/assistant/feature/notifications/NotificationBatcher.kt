package com.jarvis.assistant.feature.notifications

import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * A notification that has been batched from multiple related [ProactiveAlert]s.
 */
data class BatchedNotification(
    val groupKey: String,
    val alerts: List<ProactiveAlert>,
    val summary: String,
    val priority: NotificationPriority,
)

/**
 * Groups related proactive alerts and creates summary notifications.
 *
 * Alerts sharing the same [ProactiveAlert.groupKey] are buffered. A group is
 * flushed when it reaches [BATCH_SIZE] alerts or when the oldest alert in
 * the group is older than [MAX_AGE_MS].
 */
@Singleton
class NotificationBatcher @Inject constructor() {

    private val buffer = ConcurrentHashMap<String, MutableList<ProactiveAlert>>()

    /**
     * Add an alert to the batch buffer. Flushes any groups that have reached
     * the batch threshold or age limit and returns the resulting batched
     * notifications (may be empty if nothing is ready to flush).
     */
    @Synchronized
    fun addAndFlush(alert: ProactiveAlert): List<BatchedNotification> {
        buffer.getOrPut(alert.groupKey) {
            Collections.synchronizedList(mutableListOf())
        }.add(alert)

        val results = mutableListOf<BatchedNotification>()
        val now = System.currentTimeMillis()

        val iter = buffer.entries.iterator()
        while (iter.hasNext()) {
            val (key, alerts) = iter.next()
            if (alerts.isEmpty()) {
                iter.remove()
                continue
            }
            val oldest = alerts.minOf { it.receivedAt }
            if (alerts.size >= BATCH_SIZE || (now - oldest > MAX_AGE_MS)) {
                results.add(createBatch(key, alerts.toList()))
                iter.remove()
            }
        }

        return results
    }

    /**
     * Flush all pending groups regardless of size or age.
     * Called on context change or settings change.
     */
    @Synchronized
    fun flushAll(): List<BatchedNotification> {
        val results = mutableListOf<BatchedNotification>()
        val iter = buffer.entries.iterator()
        while (iter.hasNext()) {
            val (key, alerts) = iter.next()
            if (alerts.isNotEmpty()) {
                results.add(createBatch(key, alerts.toList()))
            }
            iter.remove()
        }
        return results
    }

    private fun createBatch(groupKey: String, alerts: List<ProactiveAlert>): BatchedNotification {
        // Use the highest priority alert's priority for the batch
        val highestPriority = alerts.minByOrNull { it.priority.ordinal }?.priority
            ?: NotificationPriority.BACKGROUND

        val summaryParts = alerts.map { it.title }.distinct().take(MAX_SUMMARY_ITEMS)
        val summary = if (alerts.size <= MAX_SUMMARY_ITEMS) {
            "${alerts.size} alerts: ${summaryParts.joinToString(", ")}"
        } else {
            "${alerts.size} alerts: ${summaryParts.joinToString(", ")} and ${alerts.size - MAX_SUMMARY_ITEMS} more"
        }

        return BatchedNotification(
            groupKey = groupKey,
            alerts = alerts,
            summary = summary,
            priority = highestPriority,
        )
    }

    companion object {
        /** Minimum alerts in a group before automatic flush. */
        private const val BATCH_SIZE = 3

        /** Maximum age (ms) of the oldest alert before a group is flushed. */
        private const val MAX_AGE_MS = 5L * 60 * 1000 // 5 minutes

        /** Maximum individual alert titles shown in summary text. */
        private const val MAX_SUMMARY_ITEMS = 5
    }
}
