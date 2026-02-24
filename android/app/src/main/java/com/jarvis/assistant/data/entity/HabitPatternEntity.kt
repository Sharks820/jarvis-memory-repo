package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Room entity for detected behavioral patterns.
 *
 * Patterns are detected by [PatternDetector] from phone usage, location, and time data.
 * Each pattern has a confidence score (0.0-1.0) that must reach >= 0.6 before nudges
 * are generated. Patterns can be user-deactivated or auto-suppressed when the user
 * consistently ignores their nudges.
 */
@Entity(tableName = "habit_patterns")
data class HabitPatternEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    /** One of "time_based", "location_based", "usage_based", "built_in". */
    val patternType: String,
    /** Human-readable label (e.g. "Gym workout", "Water reminder", "Screen break"). */
    val label: String,
    /** The nudge text (e.g. "You usually work out at 5pm on Tuesdays"). */
    val description: String,
    /** JSON array of day-of-week ints (1=Mon..7=Sun), e.g. "[2,4]" for Tue/Thu. */
    val triggerDays: String,
    /** Hour (0-23) when the pattern typically occurs. */
    val triggerHour: Int,
    /** Minute (0-59). */
    val triggerMinute: Int,
    /** Optional location association ("home", "work", "gym", or empty). */
    val locationLabel: String = "",
    /** 0.0-1.0, how confident the detection is. Patterns need >= 0.6 for nudges. */
    val confidence: Float = 0f,
    /** How many times this pattern has been observed. */
    val occurrenceCount: Int = 0,
    /** Default true, user can disable specific patterns. */
    val isActive: Boolean = true,
    /** Default false, set true by adaptive suppression. */
    val isSuppressed: Boolean = false,
    /** One of "fitness", "health", "productivity", "social", "sleep", "custom". */
    val category: String = "custom",
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
)
