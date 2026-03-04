package com.jarvis.assistant.feature.health

import android.util.Log
import com.jarvis.assistant.data.dao.SleepSessionDao
import com.jarvis.assistant.data.entity.SleepSessionEntity
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Estimates sleep quality from screen on/off patterns.
 *
 * Finds the longest screen-off gap between 8pm and noon (next day) as the
 * sleep window. Counts screen-on events during that window as interruptions.
 * Quality score (0-100) penalises short duration and frequent interruptions.
 *
 * Should be called once per morning via [SyncWorker] with a 6-hour throttle.
 */
@Singleton
class SleepEstimator @Inject constructor(
    private val screenStateTracker: ScreenStateTracker,
    private val sleepSessionDao: SleepSessionDao,
) {
    companion object {
        private const val TAG = "SleepEstimator"
        private const val MIN_SLEEP_HOURS = 3
        private const val MAX_SLEEP_HOURS = 14
        private const val MIN_GAP_MS = MIN_SLEEP_HOURS * 3_600_000L
        private const val MAX_GAP_MS = MAX_SLEEP_HOURS * 3_600_000L
    }

    suspend fun estimateLastNight(): SleepSessionEntity? {
        val now = System.currentTimeMillis()
        val yesterday6pm = now - TimeUnit.HOURS.toMillis(18)
        val events = screenStateTracker.getEventsSince(yesterday6pm)

        if (events.isEmpty()) return null

        // Find the longest screen-off gap between 8pm-noon as sleep window
        val offEvents = events.filter { !it.isOn }
        val onEvents = events.filter { it.isOn }

        var bestOnset = 0L
        var bestWake = 0L
        var bestDuration = 0L

        for (off in offEvents) {
            val hour = Instant.ofEpochMilli(off.timestamp).atZone(ZoneId.systemDefault()).hour
            if (hour in 13..19) continue // Skip afternoon/early evening offs

            val nextOn = onEvents.filter { it.timestamp > off.timestamp }.minByOrNull { it.timestamp }
            val wake = nextOn?.timestamp ?: continue
            val duration = wake - off.timestamp

            if (duration in MIN_GAP_MS..MAX_GAP_MS && duration > bestDuration) {
                bestOnset = off.timestamp
                bestWake = wake
                bestDuration = duration
            }
        }

        if (bestDuration == 0L) return null

        // Count interruptions (screen-on events during sleep window)
        val interruptions = events.count { it.isOn && it.timestamp in bestOnset..bestWake }

        val durationMin = (bestDuration / 60_000).toInt()
        val quality = computeQuality(durationMin, interruptions)
        val date = Instant.ofEpochMilli(bestWake).atZone(ZoneId.systemDefault())
            .toLocalDate().format(DateTimeFormatter.ofPattern("yyyy-MM-dd"))

        // Skip if already recorded for this date
        val existing = sleepSessionDao.getLatest()
        if (existing?.date == date) return existing

        val session = SleepSessionEntity(
            onsetTime = bestOnset,
            wakeTime = bestWake,
            durationMinutes = durationMin,
            qualityScore = quality,
            interruptions = interruptions,
            date = date,
        )
        sleepSessionDao.insert(session)
        Log.i(TAG, "Sleep recorded: ${durationMin}min, quality=$quality, interruptions=$interruptions")
        return session
    }

    private fun computeQuality(durationMin: Int, interruptions: Int): Int {
        var score = 80
        // Duration: ideal is 420-480 min (7-8 hours)
        val durationHours = durationMin / 60.0
        if (durationHours < 6) score -= ((6 - durationHours) * 10).toInt()
        if (durationHours > 9) score -= ((durationHours - 9) * 5).toInt()
        // Interruptions
        score -= interruptions * 5
        return score.coerceIn(0, 100)
    }
}
