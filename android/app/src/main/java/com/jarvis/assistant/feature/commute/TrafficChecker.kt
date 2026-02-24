package com.jarvis.assistant.feature.commute

import android.app.NotificationManager
import android.content.Context
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.feature.notifications.NotificationChannelManager
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Calendar
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Pre-departure traffic checker that suggests leave times before commutes.
 *
 * Uses learned home/work locations and their average departure times to
 * determine when the user is about to commute. Sends a query to the desktop
 * brain for traffic data; falls back to a simple time-based suggestion if
 * the desktop doesn't have real-time traffic info.
 *
 * NOTE: Real-time traffic APIs (Google Maps, HERE) require API keys and cloud
 * costs. This initial implementation uses the desktop brain as a proxy.
 */
@Singleton
class TrafficChecker @Inject constructor(
    @ApplicationContext private val context: Context,
    private val commuteDao: CommuteDao,
    private val apiClient: JarvisApiClient,
    private val channelManager: NotificationChannelManager,
) {

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    /**
     * Check if the user is about to commute and provide traffic suggestions.
     *
     * Called periodically from [JarvisService] (every 30 minutes).
     */
    suspend fun checkPreDeparture() {
        val home = commuteDao.getLocationByLabel("home")
        val work = commuteDao.getLocationByLabel("work")

        if (home == null && work == null) {
            Log.d(TAG, "No learned locations yet, skipping traffic check")
            return
        }

        val currentHour = Calendar.getInstance().let {
            it.get(Calendar.HOUR_OF_DAY) + it.get(Calendar.MINUTE) / 60.0f
        }

        // Determine commute direction based on time of day
        val destination: String
        val origin: Pair<Double, Double>?
        val dest: Pair<Double, Double>?

        when {
            // Within 1 hour before home's avg departure -> heading to work
            home != null && work != null &&
                currentHour >= home.avgDepartureHour - 1.0f &&
                currentHour <= home.avgDepartureHour + 0.5f -> {
                destination = "work"
                origin = Pair(home.latitude, home.longitude)
                dest = Pair(work.latitude, work.longitude)
            }
            // Within 1 hour before work's avg departure -> heading home
            work != null && home != null &&
                currentHour >= work.avgDepartureHour - 1.0f &&
                currentHour <= work.avgDepartureHour + 0.5f -> {
                destination = "home"
                origin = Pair(work.latitude, work.longitude)
                dest = Pair(home.latitude, home.longitude)
            }
            else -> {
                Log.d(TAG, "Not within commute window")
                return
            }
        }

        // Try to get traffic data from desktop brain
        val trafficMessage = try {
            val commandText = "Jarvis, check traffic from " +
                "${origin.first},${origin.second} to ${dest.first},${dest.second}"
            val response = apiClient.api().sendCommand(
                CommandRequest(text = commandText),
            )
            val responseText = response.stdoutTail.joinToString(" ")
            if (responseText.isNotBlank() && !responseText.contains("don't have")) {
                "Commute to $destination: $responseText"
            } else {
                null
            }
        } catch (e: Exception) {
            Log.d(TAG, "Desktop traffic query failed: ${e.message}")
            null
        }

        // Fall back to time-based suggestion
        val avgDep = if (destination == "work") {
            home?.avgDepartureHour ?: return
        } else {
            work?.avgDepartureHour ?: return
        }

        val message = trafficMessage
            ?: "Your typical commute to $destination starts around " +
            "${getLeaveTimeSuggestion(destination, avgDep)}. Consider checking traffic."

        postNotification(message)
    }

    /**
     * Format a human-readable leave-time suggestion.
     */
    fun getLeaveTimeSuggestion(destinationLabel: String, avgDepartureHour: Float): String {
        val hour = avgDepartureHour.toInt()
        val minute = ((avgDepartureHour - hour) * 60).toInt()
        val amPm = if (hour < 12) "AM" else "PM"
        val displayHour = when {
            hour == 0 -> 12
            hour > 12 -> hour - 12
            else -> hour
        }
        return "%d:%02d %s".format(displayHour, minute, amPm)
    }

    private fun postNotification(message: String) {
        try {
            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.IMPORTANT.channelId,
            )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Commute Alert")
                .setContentText(message)
                .setStyle(NotificationCompat.BigTextStyle().bigText(message))
                .setAutoCancel(true)
                .build()

            notificationManager.notify(NOTIFICATION_TAG, NOTIFICATION_ID, notification)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to post traffic notification: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "TrafficChecker"
        private const val NOTIFICATION_TAG = "commute_alert"
        private const val NOTIFICATION_ID = 5001
    }
}
