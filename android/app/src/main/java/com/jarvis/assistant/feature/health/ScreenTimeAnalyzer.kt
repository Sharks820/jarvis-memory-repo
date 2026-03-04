package com.jarvis.assistant.feature.health

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Process
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import java.time.LocalDate
import java.time.ZoneId
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Analyses per-app screen time via [UsageStatsManager].
 *
 * Categorises packages into social-media, entertainment, productivity,
 * and communication buckets. Provides daily totals, late-night usage
 * tracking (10 pm – 3 am), and week-over-week comparison insights.
 *
 * Requires `PACKAGE_USAGE_STATS` permission — the user must grant it
 * manually via Settings → Apps → Special Access → Usage Access.
 */
@Singleton
class ScreenTimeAnalyzer @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    companion object {
        private const val TAG = "ScreenTimeAnalyzer"

        private val SOCIAL_MEDIA = setOf(
            "com.instagram.android",
            "com.twitter.android",
            "com.zhiliaoapp.musically", // TikTok
            "com.snapchat.android",
            "com.facebook.katana",
            "com.facebook.orca", // Messenger
            "com.reddit.frontpage",
            "com.linkedin.android",
        )

        private val ENTERTAINMENT = setOf(
            "com.google.android.youtube",
            "com.netflix.mediaclient",
            "com.spotify.music",
            "com.hulu.livingroomplus",
            "com.disney.disneyplus",
            "tv.twitch.android.app",
        )

        private val PRODUCTIVITY = setOf(
            "com.google.android.apps.docs",
            "com.google.android.apps.docs.editors.docs",
            "com.google.android.apps.docs.editors.sheets",
            "com.google.android.apps.docs.editors.slides",
            "com.google.android.calendar",
            "com.google.android.apps.tasks",
            "com.google.android.keep",
            "com.microsoft.office.outlook",
            "com.todoist",
            "com.notion.id",
        )

        private val COMMUNICATION = setOf(
            "com.whatsapp",
            "org.thoughtcrime.securesms", // Signal
            "org.telegram.messenger",
            "com.google.android.apps.messaging",
            "com.Slack",
            "us.zoom.videomeetings",
            "com.discord",
        )
    }

    data class CategoryUsage(
        val socialMinutes: Int = 0,
        val entertainmentMinutes: Int = 0,
        val productivityMinutes: Int = 0,
        val communicationMinutes: Int = 0,
        val otherMinutes: Int = 0,
    ) {
        val totalMinutes: Int
            get() = socialMinutes + entertainmentMinutes + productivityMinutes +
                communicationMinutes + otherMinutes
    }

    fun hasPermission(): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = appOps.unsafeCheckOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            Process.myUid(),
            context.packageName,
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    /**
     * Returns per-category foreground time for the given [date].
     */
    fun getUsageForDay(date: LocalDate): CategoryUsage {
        if (!hasPermission()) return CategoryUsage()

        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE)
            as? UsageStatsManager ?: return CategoryUsage()

        val zone = ZoneId.systemDefault()
        val startMs = date.atStartOfDay(zone).toInstant().toEpochMilli()
        val endMs = date.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()

        val stats = usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, startMs, endMs)
        if (stats.isNullOrEmpty()) return CategoryUsage()

        var social = 0L; var entertainment = 0L; var productivity = 0L
        var communication = 0L; var other = 0L

        for (stat in stats) {
            val mins = stat.totalTimeInForeground / 60_000
            if (mins <= 0) continue
            val pkg = stat.packageName
            when {
                pkg in SOCIAL_MEDIA -> social += mins
                pkg in ENTERTAINMENT -> entertainment += mins
                pkg in PRODUCTIVITY -> productivity += mins
                pkg in COMMUNICATION -> communication += mins
                else -> other += mins
            }
        }

        return CategoryUsage(
            socialMinutes = social.toInt(),
            entertainmentMinutes = entertainment.toInt(),
            productivityMinutes = productivity.toInt(),
            communicationMinutes = communication.toInt(),
            otherMinutes = other.toInt(),
        )
    }

    /**
     * Returns total social-media minutes used between 10 pm and 3 am on the
     * given [date] (10 pm of [date] through 3 am of [date]+1).
     */
    fun getLateNightUsage(date: LocalDate): Int {
        if (!hasPermission()) return 0

        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE)
            as? UsageStatsManager ?: return 0

        val zone = ZoneId.systemDefault()
        val lateStart = date.atTime(22, 0).atZone(zone).toInstant().toEpochMilli()
        val lateEnd = date.plusDays(1).atTime(3, 0).atZone(zone).toInstant().toEpochMilli()

        var totalMs = 0L
        var lastResumeTime = 0L
        var currentPkg: String? = null

        val events = usm.queryEvents(lateStart, lateEnd)
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            when (event.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> {
                    if (event.packageName in SOCIAL_MEDIA) {
                        lastResumeTime = event.timeStamp
                        currentPkg = event.packageName
                    } else {
                        if (currentPkg != null && lastResumeTime > 0) {
                            totalMs += event.timeStamp - lastResumeTime
                        }
                        currentPkg = null
                        lastResumeTime = 0
                    }
                }
                UsageEvents.Event.ACTIVITY_PAUSED -> {
                    if (currentPkg != null && event.packageName == currentPkg && lastResumeTime > 0) {
                        totalMs += event.timeStamp - lastResumeTime
                        currentPkg = null
                        lastResumeTime = 0
                    }
                }
            }
        }

        return (totalMs / 60_000).toInt()
    }

    /**
     * Compares this week's category usage against the previous week.
     * Returns a human-readable summary for the health briefing.
     */
    fun getWeeklyInsights(): String {
        if (!hasPermission()) return "Screen time analytics require usage access permission."

        val today = LocalDate.now()
        val thisWeekStart = today.minusDays(6)
        val lastWeekStart = today.minusDays(13)
        val lastWeekEnd = today.minusDays(7)

        val thisWeek = aggregateRange(thisWeekStart, today)
        val lastWeek = aggregateRange(lastWeekStart, lastWeekEnd)

        val parts = mutableListOf<String>()

        val totalDelta = thisWeek.totalMinutes - lastWeek.totalMinutes
        val direction = if (totalDelta > 0) "up" else "down"
        parts.add("Total screen time: ${formatMinutes(thisWeek.totalMinutes)} ($direction ${formatMinutes(kotlin.math.abs(totalDelta))} from last week)")

        if (thisWeek.socialMinutes > 0) {
            val socialDelta = thisWeek.socialMinutes - lastWeek.socialMinutes
            if (kotlin.math.abs(socialDelta) > 15) {
                val d = if (socialDelta > 0) "up" else "down"
                parts.add("Social media: ${formatMinutes(thisWeek.socialMinutes)} ($d ${formatMinutes(kotlin.math.abs(socialDelta))})")
            }
        }

        val avgLateNight = (0..6).map { getLateNightUsage(today.minusDays(it.toLong())) }.average()
        if (avgLateNight > 10) {
            parts.add("Late-night social avg: ${avgLateNight.toInt()} min/night")
        }

        return parts.joinToString(". ") + "."
    }

    private fun aggregateRange(start: LocalDate, end: LocalDate): CategoryUsage {
        var social = 0; var entertainment = 0; var productivity = 0
        var communication = 0; var other = 0
        var d = start
        while (!d.isAfter(end)) {
            val usage = getUsageForDay(d)
            social += usage.socialMinutes
            entertainment += usage.entertainmentMinutes
            productivity += usage.productivityMinutes
            communication += usage.communicationMinutes
            other += usage.otherMinutes
            d = d.plusDays(1)
        }
        return CategoryUsage(social, entertainment, productivity, communication, other)
    }

    private fun formatMinutes(minutes: Int): String = when {
        minutes >= 60 -> "${minutes / 60}h ${minutes % 60}m"
        else -> "${minutes}m"
    }
}
