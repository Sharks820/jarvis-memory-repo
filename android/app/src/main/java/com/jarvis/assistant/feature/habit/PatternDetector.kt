package com.jarvis.assistant.feature.habit

import android.content.Context
import android.util.Log
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.entity.HabitPatternEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Detects recurring behavioral patterns from phone usage, location, and time data.
 *
 * Pattern detection is intentionally simple and rule-based (no ML). It relies on
 * existing data sources: ContextStateEntity timestamps, CommuteLocationEntity visit
 * patterns, and time-of-day clustering.
 *
 * Called periodically (once per day) from [JarvisService].
 */
@Singleton
class PatternDetector @Inject constructor(
    private val habitDao: HabitDao,
    private val contextStateDao: ContextStateDao,
    private val commuteDao: CommuteDao,
    private val builtInNudges: BuiltInNudges,
    @ApplicationContext private val context: Context,
) {

    /**
     * Main detection loop called periodically from JarvisService.
     *
     * 1. Detects time-based patterns from context state history
     * 2. Detects location-based patterns from commute data
     * 3. Seeds built-in nudge patterns
     */
    suspend fun detectPatterns() {
        try {
            detectTimeBasedPatterns()
        } catch (e: Exception) {
            Log.w(TAG, "Time-based pattern detection error: ${e.message}")
        }

        try {
            detectLocationBasedPatterns()
        } catch (e: Exception) {
            Log.w(TAG, "Location-based pattern detection error: ${e.message}")
        }

        try {
            builtInNudges.ensureBuiltInPatterns()
        } catch (e: Exception) {
            Log.w(TAG, "Built-in pattern seeding error: ${e.message}")
        }

        Log.i(TAG, "Pattern detection cycle complete")
    }

    /**
     * Query context state for the last 14 days. Group by day-of-week + hour.
     * If the same context appears on the same day-of-week + hour window
     * (within +/- 30 minutes) at least 3 times in 14 days, create or update
     * a HabitPatternEntity.
     */
    private suspend fun detectTimeBasedPatterns() {
        val fourteenDaysAgo = System.currentTimeMillis() - 14L * 24 * 60 * 60 * 1000
        val recentStates = contextStateDao.getStatesSince(fourteenDaysAgo)

        if (recentStates.isEmpty()) return

        // Group by (context, dayOfWeek, hour)
        data class TimeSlot(val context: String, val dayOfWeek: Int, val hour: Int)

        val groups = mutableMapOf<TimeSlot, MutableList<Long>>()

        for (state in recentStates) {
            val dateTime = LocalDateTime.ofInstant(
                Instant.ofEpochMilli(state.createdAt),
                ZoneId.systemDefault(),
            )
            val slot = TimeSlot(
                context = state.context,
                dayOfWeek = dateTime.dayOfWeek.value, // 1=Mon..7=Sun
                hour = dateTime.hour,
            )
            groups.getOrPut(slot) { mutableListOf() }.add(state.createdAt)
        }

        for ((slot, timestamps) in groups) {
            if (timestamps.size < MIN_OCCURRENCES) continue

            val confidence = calculateConfidence(timestamps.size, DETECTION_WINDOW_DAYS)
            if (confidence < MIN_CONFIDENCE) continue

            val label = "Routine: ${slot.context} on ${dayName(slot.dayOfWeek)} at ${slot.hour}:00"
            val description = "You're usually in ${slot.context} mode around ${slot.hour}:00 on ${dayName(slot.dayOfWeek)}s"

            val existing = habitDao.findByTypeAndLabel("time_based", label)
            if (existing != null) {
                habitDao.incrementOccurrence(existing.id, confidence)
            } else {
                habitDao.insert(
                    HabitPatternEntity(
                        patternType = "time_based",
                        label = label,
                        description = description,
                        triggerDays = "[${slot.dayOfWeek}]",
                        triggerHour = slot.hour,
                        triggerMinute = 0,
                        confidence = confidence,
                        occurrenceCount = timestamps.size,
                        category = categorizeContext(slot.context),
                    ),
                )
            }
        }
    }

    /**
     * Query commute locations for consistent visit patterns.
     * If the user visits a non-home, non-work location at consistent times
     * (same day-of-week, similar hour), create a location-based pattern.
     */
    private suspend fun detectLocationBasedPatterns() {
        val locations = commuteDao.getAllLocations()

        for (location in locations) {
            // Skip home and work -- those are always visited
            if (location.label == "home" || location.label == "work") continue
            if (location.visitCount < MIN_OCCURRENCES) continue

            val confidence = calculateConfidence(location.visitCount, 14)
            if (confidence < MIN_CONFIDENCE) continue

            val arrivalHour = location.avgArrivalHour.toInt()
            val label = "Visit: ${location.label}"
            val description = "You usually visit ${location.label} around $arrivalHour:00"

            val existing = habitDao.findByTypeAndLabel("location_based", label)
            if (existing != null) {
                habitDao.incrementOccurrence(existing.id, confidence)
            } else {
                // Infer trigger days from visit count -- if enough visits, assume regular
                val triggerDays = "[1,2,3,4,5,6,7]" // Default to every day for frequent locations
                habitDao.insert(
                    HabitPatternEntity(
                        patternType = "location_based",
                        label = label,
                        description = description,
                        triggerDays = triggerDays,
                        triggerHour = arrivalHour,
                        triggerMinute = 0,
                        locationLabel = location.label,
                        confidence = confidence,
                        occurrenceCount = location.visitCount,
                        category = "fitness", // Most tracked non-home/work locations are gyms etc.
                    ),
                )
            }
        }
    }

    private fun calculateConfidence(occurrences: Int, totalDays: Int): Float {
        return (occurrences.toFloat() / totalDays.coerceAtLeast(1)).coerceIn(0f, 1f)
    }

    private fun dayName(dayOfWeek: Int): String = when (dayOfWeek) {
        1 -> "Monday"
        2 -> "Tuesday"
        3 -> "Wednesday"
        4 -> "Thursday"
        5 -> "Friday"
        6 -> "Saturday"
        7 -> "Sunday"
        else -> "Day $dayOfWeek"
    }

    private fun categorizeContext(context: String): String = when (context.uppercase()) {
        "MEETING" -> "productivity"
        "DRIVING" -> "productivity"
        "SLEEPING" -> "sleep"
        "GAMING" -> "social"
        "EXERCISE", "GYM" -> "fitness"
        else -> "custom"
    }

    companion object {
        private const val TAG = "PatternDetector"
        private const val DETECTION_WINDOW_DAYS = 14
        private const val MIN_OCCURRENCES = 3
        private const val MIN_CONFIDENCE = 0.2f
    }
}
