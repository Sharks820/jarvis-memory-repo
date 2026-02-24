package com.jarvis.assistant.feature.commute

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.util.Log
import androidx.core.content.ContextCompat
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.entity.CommuteLocationEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Calendar
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Learns home and work locations automatically from GPS patterns.
 *
 * Records the user's position periodically (called from [JarvisService] every
 * 15 minutes). When a location is visited repeatedly, it is promoted from
 * "frequent" to "home" (evening/night visits) or "work" (weekday business hours).
 */
@Singleton
class LocationLearner @Inject constructor(
    @ApplicationContext private val context: Context,
    private val commuteDao: CommuteDao,
) {

    /**
     * Record the current GPS location and update learned location patterns.
     *
     * Skips silently if location permission is not granted or GPS is unavailable.
     */
    @Suppress("MissingPermission")
    suspend fun recordLocation() {
        if (!hasLocationPermission()) {
            Log.d(TAG, "Location permission not granted, skipping")
            return
        }

        val locationManager =
            context.getSystemService(Context.LOCATION_SERVICE) as LocationManager

        val location = try {
            locationManager.getLastKnownLocation(LocationManager.FUSED_PROVIDER)
                ?: locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER)
        } catch (e: Exception) {
            Log.d(TAG, "Failed to get location: ${e.message}")
            null
        }

        if (location == null || location.accuracy > MAX_ACCURACY_METERS) {
            Log.d(TAG, "Location unavailable or too inaccurate (${location?.accuracy}m)")
            return
        }

        val lat = location.latitude
        val lon = location.longitude
        val currentHour = Calendar.getInstance().let {
            it.get(Calendar.HOUR_OF_DAY) + it.get(Calendar.MINUTE) / 60.0f
        }

        // Check against known locations
        val allLocations = commuteDao.getAllLocations()
        val nearby = allLocations.firstOrNull { existing ->
            haversineDistance(lat, lon, existing.latitude, existing.longitude) <= existing.radius
        }

        if (nearby != null) {
            // Update existing location
            val newVisitCount = nearby.visitCount + 1
            val newConfidence = (newVisitCount / 20.0f).coerceAtMost(1.0f)
            val newAvgArrival = runningAverage(nearby.avgArrivalHour, currentHour, newVisitCount)
            val newAvgDeparture = runningAverage(
                nearby.avgDepartureHour, currentHour, newVisitCount,
            )

            var updatedLabel = nearby.label
            if (newVisitCount >= CLASSIFY_THRESHOLD && nearby.label == "frequent") {
                updatedLabel = classifyLocation(nearby, newAvgArrival, newAvgDeparture)
            }

            commuteDao.updateLocation(
                nearby.copy(
                    visitCount = newVisitCount,
                    avgArrivalHour = newAvgArrival,
                    avgDepartureHour = newAvgDeparture,
                    lastVisited = System.currentTimeMillis(),
                    confidence = newConfidence,
                    label = updatedLabel,
                ),
            )
        } else {
            // Insert new frequent location
            commuteDao.insertLocation(
                CommuteLocationEntity(
                    label = "frequent",
                    latitude = lat,
                    longitude = lon,
                    avgArrivalHour = currentHour,
                    avgDepartureHour = currentHour,
                ),
            )
        }
    }

    /** Get the learned home location, or null if not yet classified. */
    suspend fun getHomeLocation(): CommuteLocationEntity? =
        commuteDao.getLocationByLabel("home")

    /** Get the learned work location, or null if not yet classified. */
    suspend fun getWorkLocation(): CommuteLocationEntity? =
        commuteDao.getLocationByLabel("work")

    /**
     * Haversine distance between two lat/lon points.
     *
     * @return Distance in meters.
     */
    fun haversineDistance(
        lat1: Double,
        lon1: Double,
        lat2: Double,
        lon2: Double,
    ): Double {
        val r = EARTH_RADIUS_METERS
        val dLat = Math.toRadians(lat2 - lat1)
        val dLon = Math.toRadians(lon2 - lon1)
        val a = sin(dLat / 2) * sin(dLat / 2) +
            cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) *
            sin(dLon / 2) * sin(dLon / 2)
        val c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return r * c
    }

    // ── Private Helpers ───────────────────────────────────────────────

    private fun hasLocationPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_FINE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun runningAverage(currentAvg: Float, newValue: Float, count: Int): Float {
        if (count <= 1) return newValue
        return currentAvg + (newValue - currentAvg) / count
    }

    /**
     * Classify a frequently-visited location based on time-of-day patterns.
     *
     * - "home" if average arrival is in evening/night hours (18:00-08:00)
     * - "work" if average arrival is during business hours on weekdays (08:00-18:00)
     */
    private fun classifyLocation(
        location: CommuteLocationEntity,
        avgArrival: Float,
        avgDeparture: Float,
    ): String {
        val isEveningArrival = avgArrival >= 18.0f || avgArrival < 8.0f
        val isDaytimeArrival = avgArrival in 8.0f..18.0f

        return when {
            isEveningArrival -> "home"
            isDaytimeArrival -> "work"
            else -> "frequent"
        }
    }

    companion object {
        private const val TAG = "LocationLearner"

        /** Maximum acceptable GPS accuracy in meters. */
        private const val MAX_ACCURACY_METERS = 100f

        /** Minimum visits before auto-classifying a "frequent" location. */
        private const val CLASSIFY_THRESHOLD = 5

        /** Earth radius in meters for haversine formula. */
        private const val EARTH_RADIUS_METERS = 6_371_000.0
    }
}
