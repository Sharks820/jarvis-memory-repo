package com.jarvis.assistant.feature.habit

import android.content.Context
import android.util.Log
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.entity.HabitPatternEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Provides built-in nudge definitions: water reminders, screen time awareness,
 * and sleep schedule.
 *
 * Built-in nudges are created with [isActive] = false by default so the user
 * must opt-in via Settings. The Settings UI provides toggles to activate each
 * built-in type.
 */
@Singleton
class BuiltInNudges @Inject constructor(
    private val habitDao: HabitDao,
    @ApplicationContext private val context: Context,
) {

    /**
     * Create built-in nudge patterns if they don't already exist.
     * Each built-in is checked via [habitDao.findByTypeAndLabel] to avoid duplicates.
     * Built-in patterns start inactive (user must opt-in via Settings).
     */
    suspend fun ensureBuiltInPatterns() {
        val allDays = "[1,2,3,4,5,6,7]"

        // Water reminders: 10:00, 13:00, 16:00
        ensurePattern(
            label = "Water Reminder",
            description = "Time to drink some water! Stay hydrated.",
            triggerHour = 10,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "health",
        )
        ensurePattern(
            label = "Water Reminder",
            description = "Afternoon hydration check - have some water!",
            triggerHour = 13,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "health",
            labelSuffix = " (Afternoon)",
        )
        ensurePattern(
            label = "Water Reminder",
            description = "Evening water reminder - stay hydrated through the day!",
            triggerHour = 16,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "health",
            labelSuffix = " (Evening)",
        )

        // Screen break reminders: 11:00, 15:00, 20:00
        ensurePattern(
            label = "Screen Break",
            description = "You've been on your phone for a while. Take a quick break for your eyes.",
            triggerHour = 11,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "health",
        )
        ensurePattern(
            label = "Screen Break",
            description = "Time for an afternoon screen break. Look at something far away for 20 seconds.",
            triggerHour = 15,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "health",
            labelSuffix = " (Afternoon)",
        )
        ensurePattern(
            label = "Screen Break",
            description = "Evening screen time check. Consider putting the phone down soon.",
            triggerHour = 20,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "health",
            labelSuffix = " (Evening)",
        )

        // Sleep reminder: 22:00
        ensurePattern(
            label = "Sleep Reminder",
            description = "It's getting close to bedtime. Start winding down for better sleep.",
            triggerHour = 22,
            triggerMinute = 0,
            triggerDays = allDays,
            category = "sleep",
        )

        Log.i(TAG, "Built-in nudge patterns ensured")
    }

    /**
     * Get all active built-in type patterns.
     */
    suspend fun getActiveBuiltIns(): List<HabitPatternEntity> {
        return habitDao.getActivePatterns().filter { it.patternType == "built_in" }
    }

    private suspend fun ensurePattern(
        label: String,
        description: String,
        triggerHour: Int,
        triggerMinute: Int,
        triggerDays: String,
        category: String,
        labelSuffix: String = "",
    ) {
        val fullLabel = "$label$labelSuffix"
        val existing = habitDao.findByTypeAndLabel("built_in", fullLabel)
        if (existing != null) return // Already exists

        habitDao.insert(
            HabitPatternEntity(
                patternType = "built_in",
                label = fullLabel,
                description = description,
                triggerDays = triggerDays,
                triggerHour = triggerHour,
                triggerMinute = triggerMinute,
                confidence = 1.0f, // Built-in patterns are always confident
                occurrenceCount = 0,
                isActive = false, // User must opt-in via Settings
                category = category,
            ),
        )
    }

    companion object {
        private const val TAG = "BuiltInNudges"

        // Labels used to identify built-in nudge groups for toggle control
        const val LABEL_WATER = "Water Reminder"
        const val LABEL_SCREEN_BREAK = "Screen Break"
        const val LABEL_SLEEP = "Sleep Reminder"
    }
}
