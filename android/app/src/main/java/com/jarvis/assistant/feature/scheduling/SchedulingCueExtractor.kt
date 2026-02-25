package com.jarvis.assistant.feature.scheduling

import android.util.Log
import java.security.MessageDigest
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.time.temporal.TemporalAdjusters
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Extracted scheduling cue from notification text.
 *
 * @property title inferred event title (first line or text before date, max 100 chars)
 * @property dateTime epoch millis of start time (0 if only date matched, no time)
 * @property endDateTime epoch millis of end time (0 if not specified, caller defaults to start + 1 hr)
 * @property location extracted location string (empty if none found)
 * @property sourcePackage Android package name of the notification source
 * @property sourceText original notification text
 * @property confidence 0.0-1.0 based on how many cue types matched
 */
data class SchedulingCue(
    val title: String,
    val dateTime: Long,
    val endDateTime: Long,
    val location: String,
    val sourcePackage: String,
    val sourceText: String,
    val confidence: Float,
)

/**
 * Regex-based date/time/location extraction from notification text.
 *
 * Parses scheduling cues from SMS and email notification bodies and returns
 * structured [SchedulingCue] objects for calendar event creation.
 */
@Singleton
class SchedulingCueExtractor @Inject constructor() {

    // ── Date patterns ────────────────────────────────────────────────────

    /** "January 15", "Jan 15", "January 15, 2026", "Jan 15, 2026" */
    private val monthDayPattern = Regex(
        """(?i)(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?""",
    )

    /** "1/15/2026", "01/15/2026", "1/15" */
    private val slashDatePattern = Regex(
        """(?<\!\d)(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?\!\d)""",
    )

    /** "2026-01-15" ISO format */
    private val isoDatePattern = Regex(
        """(\d{4})-(\d{2})-(\d{2})""",
    )

    /** Relative date patterns */
    private val tomorrowPattern = Regex("""(?i)\btomorrow\b""")
    private val nextDayPattern = Regex(
        """(?i)\bnext\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b""",
    )
    private val thisDayPattern = Regex(
        """(?i)\bthis\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b""",
    )
    private val nextWeekPattern = Regex("""(?i)\bnext\s+week\b""")

    // ── Time patterns ────────────────────────────────────────────────────

    /** "3:00 PM", "3:00pm", "10:30 AM" */
    private val colonTimePattern = Regex(
        """(\d{1,2}):(\d{2})\s*(AM|PM|am|pm|a\.m\.|p\.m\.)""",
    )

    /** "3pm", "3 PM", "10am" */
    private val shortTimePattern = Regex(
        """(?<!\d)(\d{1,2})\s*(AM|PM|am|pm|a\.m\.|p\.m\.)""",
    )

    /** "15:00" 24-hour format */
    private val militaryTimePattern = Regex(
        """(?<!\d)(\d{1,2}):(\d{2})(?!\s*(?:AM|PM|am|pm))""",
    )

    /** "at noon", "at midnight" */
    private val specialTimePattern = Regex(
        """(?i)\bat\s+(noon|midnight)\b""",
    )

    /** "at 3" (only when preceded by "at") */
    private val atHourPattern = Regex(
        """(?i)\bat\s+(\d{1,2})(?!\s*(?:AM|PM|am|pm|:|/|\d))""",
    )

    // ── Location patterns ────────────────────────────────────────────────

    /** "at [Capitalized Location]" but not time words */
    private val atLocationPattern = Regex(
        """(?i)(?:\bat\s+)((?:[A-Z][a-z]+(?:\s+[A-Z][a-z']+)*(?:\s+(?:of|the|and|&)\s+[A-Z][a-z']+)*))\b""",
    )

    /** "@ Location" */
    private val atSignLocationPattern = Regex(
        """@\s*([A-Z][A-Za-z']+(?:\s+[A-Za-z']+)*)""",
    )

    /** Street address: "123 Main St", "456 Oak Avenue" */
    private val addressPattern = Regex(
        """(\d{1,5}\s+(?:[A-Z][a-z]+\s+)+(?:St(?:reet)?|Ave(?:nue)?|Blvd|Boulevard|Dr(?:ive)?|Rd|Road|Ln|Lane|Way|Ct|Court|Pl(?:ace)?|Pkwy|Parkway|Cir(?:cle)?)\.?)""",
    )

    /** Words that look like time rather than location when following "at" */
    private val timeWords = setOf(
        "noon", "midnight", "night", "morning", "evening", "afternoon",
        "am", "pm", "oclock", "o'clock",
    )

    // ── Month mapping ────────────────────────────────────────────────────

    private val monthMap = mapOf(
        "january" to 1, "jan" to 1,
        "february" to 2, "feb" to 2,
        "march" to 3, "mar" to 3,
        "april" to 4, "apr" to 4,
        "may" to 5,
        "june" to 6, "jun" to 6,
        "july" to 7, "jul" to 7,
        "august" to 8, "aug" to 8,
        "september" to 9, "sep" to 9, "sept" to 9,
        "october" to 10, "oct" to 10,
        "november" to 11, "nov" to 11,
        "december" to 12, "dec" to 12,
    )

    private val dayOfWeekMap = mapOf(
        "monday" to DayOfWeek.MONDAY,
        "tuesday" to DayOfWeek.TUESDAY,
        "wednesday" to DayOfWeek.WEDNESDAY,
        "thursday" to DayOfWeek.THURSDAY,
        "friday" to DayOfWeek.FRIDAY,
        "saturday" to DayOfWeek.SATURDAY,
        "sunday" to DayOfWeek.SUNDAY,
    )

    /**
     * Extract scheduling cues from [text] originating from [sourcePackage].
     *
     * Returns one [SchedulingCue] per distinct date found. Returns empty list
     * if no date pattern matches.
     */
    fun extract(text: String, sourcePackage: String): List<SchedulingCue> {
        val dates = extractDates(text)
        if (dates.isEmpty()) return emptyList()

        val time = extractTime(text)
        val location = extractLocation(text)
        val title = inferTitle(text)

        return dates.map { date ->
            val startMs = computeStartMs(date, time)
            val endMs = 0L // caller handles default (start + 1hr)

            val confidence = computeConfidence(
                hasDate = true,
                hasTime = time != null,
                hasLocation = location.isNotBlank(),
                hasTitleLikeText = title.length > 5 && title.any { it.isUpperCase() },
            )

            SchedulingCue(
                title = title,
                dateTime = startMs,
                endDateTime = endMs,
                location = location,
                sourcePackage = sourcePackage,
                sourceText = text,
                confidence = confidence,
            )
        }
    }

    // ── Private extraction helpers ───────────────────────────────────────

    private fun extractDates(text: String): List<LocalDate> {
        val today = LocalDate.now()
        val results = mutableListOf<LocalDate>()

        // Relative dates first (higher priority)
        if (tomorrowPattern.containsMatchIn(text)) {
            results.add(today.plusDays(1))
        }

        nextWeekPattern.find(text)?.let {
            results.add(today.with(TemporalAdjusters.next(DayOfWeek.MONDAY)))
        }

        nextDayPattern.findAll(text).forEach { match ->
            val dayName = match.groupValues[1].lowercase(Locale.US)
            dayOfWeekMap[dayName]?.let { dow ->
                results.add(today.with(TemporalAdjusters.next(dow)))
            }
        }

        thisDayPattern.findAll(text).forEach { match ->
            val dayName = match.groupValues[1].lowercase(Locale.US)
            dayOfWeekMap[dayName]?.let { dow ->
                results.add(today.with(TemporalAdjusters.nextOrSame(dow)))
            }
        }

        // Absolute dates
        monthDayPattern.findAll(text).forEach { match ->
            val monthName = match.groupValues[1].lowercase(Locale.US)
            val day = match.groupValues[2].toIntOrNull() ?: return@forEach
            val yearStr = match.groupValues[3]
            val month = monthMap[monthName] ?: return@forEach
            val year = if (yearStr.isNotBlank()) yearStr.toInt() else today.year

            try {
                results.add(LocalDate.of(year, month, day))
            } catch (e: Exception) {
                Log.w(TAG, "Failed to parse month-day date: $monthName $day", e)
            }
        }

        slashDatePattern.findAll(text).forEach { match ->
            val month = match.groupValues[1].toIntOrNull() ?: return@forEach
            val day = match.groupValues[2].toIntOrNull() ?: return@forEach
            val yearStr = match.groupValues[3]
            val year = when {
                yearStr.isBlank() -> today.year
                yearStr.length == 2 -> 2000 + yearStr.toInt()
                else -> yearStr.toInt()
            }

            try {
                if (month in 1..12 && day in 1..31) {
                    results.add(LocalDate.of(year, month, day))
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to parse slash date: $month/$day/$yearStr", e)
            }
        }

        isoDatePattern.findAll(text).forEach { match ->
            try {
                results.add(LocalDate.parse(match.value))
            } catch (_: DateTimeParseException) { /* skip */ }
        }

        return results.distinct()
    }

    private fun extractTime(text: String): LocalTime? {
        // "3:00 PM" style
        colonTimePattern.find(text)?.let { match ->
            val hour = match.groupValues[1].toIntOrNull() ?: return@let
            val minute = match.groupValues[2].toIntOrNull() ?: return@let
            val meridiem = match.groupValues[3].uppercase().replace(".", "")
            return parseTime12(hour, minute, meridiem)
        }

        // "3pm" style
        shortTimePattern.find(text)?.let { match ->
            val hour = match.groupValues[1].toIntOrNull() ?: return@let
            val meridiem = match.groupValues[2].uppercase().replace(".", "")
            return parseTime12(hour, 0, meridiem)
        }

        // "15:00" military
        militaryTimePattern.find(text)?.let { match ->
            val hour = match.groupValues[1].toIntOrNull() ?: return@let
            val minute = match.groupValues[2].toIntOrNull() ?: return@let
            if (hour in 0..23 && minute in 0..59) {
                return LocalTime.of(hour, minute)
            }
        }

        // "noon" / "midnight"
        specialTimePattern.find(text)?.let { match ->
            return when (match.groupValues[1].lowercase(Locale.US)) {
                "noon" -> LocalTime.NOON
                "midnight" -> LocalTime.MIDNIGHT
                else -> null
            }
        }

        // "at 3" style
        atHourPattern.find(text)?.let { match ->
            val hour = match.groupValues[1].toIntOrNull() ?: return@let
            if (hour in 1..12) {
                // Assume PM for hours 1-6, AM for 7-12
                val adjustedHour = if (hour in 1..6) hour + 12 else hour
                return LocalTime.of(adjustedHour, 0)
            }
        }

        return null
    }

    private fun parseTime12(hour: Int, minute: Int, meridiem: String): LocalTime? {
        if (hour !in 1..12 || minute !in 0..59) return null
        val isPm = meridiem.startsWith("P")
        val h24 = when {
            hour == 12 && isPm -> 12
            hour == 12 && !isPm -> 0
            isPm -> hour + 12
            else -> hour
        }
        return LocalTime.of(h24, minute)
    }

    private fun extractLocation(text: String): String {
        // Try address first (most specific)
        addressPattern.find(text)?.let { return it.value.trim() }

        // Try "@ Location"
        atSignLocationPattern.find(text)?.let { match ->
            val loc = match.groupValues[1].trim()
            if (loc.isNotBlank() && loc.lowercase(Locale.US) !in timeWords) {
                return loc
            }
        }

        // Try "at [Capitalized Location]"
        atLocationPattern.findAll(text).forEach { match ->
            val loc = match.groupValues[1].trim()
            if (loc.isNotBlank() && loc.lowercase(Locale.US) !in timeWords) {
                // Ensure it's not a time reference (e.g., "at 3pm")
                val firstWord = loc.split(" ").first().lowercase(Locale.US)
                if (firstWord !in timeWords && firstWord.toIntOrNull() == null) {
                    return loc
                }
            }
        }

        return ""
    }

    private fun inferTitle(text: String): String {
        val firstLine = text.lines().firstOrNull()?.trim() ?: text.trim()
        return if (firstLine.length > 100) firstLine.take(100) else firstLine
    }

    private fun computeStartMs(date: LocalDate, time: LocalTime?): Long {
        val localDateTime = if (time != null) {
            date.atTime(time)
        } else {
            date.atStartOfDay()
        }
        return localDateTime.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }

    private fun computeConfidence(
        hasDate: Boolean,
        hasTime: Boolean,
        hasLocation: Boolean,
        hasTitleLikeText: Boolean,
    ): Float {
        if (!hasDate) return 0f
        return when {
            hasTime && hasLocation && hasTitleLikeText -> 0.9f
            hasTime && hasLocation -> 0.7f
            hasTime -> 0.5f
            else -> 0.3f
        }
    }

    companion object {
        private const val TAG = "SchedulingCueExtractor"

        /** SHA-256 hash of source text for deduplication. */
        fun contentHash(text: String): String {
            val digest = MessageDigest.getInstance("SHA-256")
            return digest.digest(text.toByteArray(Charsets.UTF_8))
                .joinToString("") { "%02x".format(it) }
        }
    }
}
