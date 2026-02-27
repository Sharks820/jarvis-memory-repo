package com.jarvis.assistant.feature.context

import android.content.ContentResolver
import android.content.Context
import android.database.Cursor
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.Uri
import android.content.ContentUris
import android.provider.CalendarContract
import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import dagger.hilt.android.qualifiers.ApplicationContext
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume
import kotlin.math.sqrt

/**
 * Detected user contexts, ordered by notification suppression aggressiveness.
 */
enum class UserContext(val label: String) {
    NORMAL("Normal"),
    MEETING("In Meeting"),
    DRIVING("Driving"),
    SLEEPING("Sleeping"),
    GAMING("Gaming"),
}

/**
 * Result of a context detection pass.
 *
 * @property context The detected [UserContext].
 * @property confidence Detection confidence (0.0 - 1.0).
 * @property source What triggered the detection (calendar, accelerometer, time, gaming_sync, manual).
 */
data class ContextState(
    val context: UserContext,
    val confidence: Float,
    val detectedAt: Long = System.currentTimeMillis(),
    val source: String,
)

/**
 * Detects the user's current context by checking multiple signals in
 * priority order: gaming sync > calendar meeting > driving (accelerometer)
 * > sleep (time window + stationary).
 *
 * Accelerometer access does not require special permissions. Sensor
 * registration is scoped to detection passes only to avoid battery drain.
 */
@Singleton
class ContextDetector @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiClient: JarvisApiClient,
) {

    private val sensorManager by lazy {
        context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    }

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * Run all detectors in priority order and return the first match.
     * Falls back to [UserContext.NORMAL] if nothing is detected.
     */
    suspend fun detectCurrentContext(): ContextState {
        // Priority 1: Gaming mode (synced from desktop)
        if (prefs.getBoolean(KEY_GAMING_SYNC, true)) {
            checkGaming()?.let { return it }
        }

        // Priority 2: Calendar meeting
        if (prefs.getBoolean(KEY_DETECT_MEETING, true)) {
            checkCalendarMeeting()?.let { return it }
        }

        // Priority 3: Driving (accelerometer heuristic)
        if (prefs.getBoolean(KEY_DETECT_DRIVING, true)) {
            checkDriving()?.let { return it }
        }

        // Priority 4: Sleep time window
        if (prefs.getBoolean(KEY_DETECT_SLEEP, true)) {
            checkSleep()?.let { return it }
        }

        return ContextState(
            context = UserContext.NORMAL,
            confidence = 1.0f,
            source = "default",
        )
    }

    // ── Gaming Detection ─────────────────────────────────────────────

    private suspend fun checkGaming(): ContextState? {
        return try {
            val settings = apiClient.api().getSettings()
            if (settings.settings?.gamingMode?.enabled == true) {
                ContextState(
                    context = UserContext.GAMING,
                    confidence = 0.95f,
                    source = "gaming_sync",
                )
            } else {
                null
            }
        } catch (e: Exception) {
            Log.d(TAG, "Gaming check failed (desktop unreachable): ${e.message}")
            null
        }
    }

    // ── Calendar Meeting Detection ───────────────────────────────────

    private fun checkCalendarMeeting(): ContextState? {
        return try {
            val now = System.currentTimeMillis()
            val resolver: ContentResolver = context.contentResolver
            val projection = arrayOf(
                CalendarContract.Instances._ID,
                CalendarContract.Instances.BEGIN,
                CalendarContract.Instances.END,
                CalendarContract.Instances.TITLE,
            )

            // Bug 6 fix: Use Instances table with time range instead of Events table.
            // Events.CONTENT_URI does not expand recurring events; Instances does.
            val startMs = now
            val endMs = now + 60_000L // 1-minute window to reliably catch overlapping events
            val builder = CalendarContract.Instances.CONTENT_URI.buildUpon()
            ContentUris.appendId(builder, startMs)
            ContentUris.appendId(builder, endMs)

            val cursor: Cursor? = resolver.query(
                builder.build(), projection, null, null, null,
            )

            cursor?.use {
                if (it.moveToFirst()) {
                    val startIdx = it.getColumnIndex(CalendarContract.Instances.BEGIN)
                    val endIdx = it.getColumnIndex(CalendarContract.Instances.END)
                    if (startIdx < 0 || endIdx < 0) return null
                    val dtStart = it.getLong(startIdx)
                    val dtEnd = it.getLong(endIdx)

                    // Confidence: higher when we're early in the meeting
                    val duration = (dtEnd - dtStart).coerceAtLeast(1)
                    val elapsed = now - dtStart
                    val progress = elapsed.toFloat() / duration
                    val confidence = (1.0f - progress * 0.3f).coerceIn(0.5f, 1.0f)

                    return ContextState(
                        context = UserContext.MEETING,
                        confidence = confidence,
                        source = "calendar",
                    )
                }
            }
            null
        } catch (e: SecurityException) {
            Log.d(TAG, "Calendar permission not granted: ${e.message}")
            null
        } catch (e: Exception) {
            Log.d(TAG, "Calendar check failed: ${e.message}")
            null
        }
    }

    // ── Driving Detection (Accelerometer Heuristic) ──────────────────

    /**
     * Sample accelerometer for [ACCEL_SAMPLE_MS] ms and check if the variance
     * suggests a driving pattern (sustained vibration between 0.5 and 5.0 m/s^2).
     */
    private suspend fun checkDriving(): ContextState? {
        val accel = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER) ?: return null

        val samples = mutableListOf<Float>()

        val result = withTimeoutOrNull(ACCEL_SAMPLE_MS + 2000L) {
            suspendCancellableCoroutine { cont ->
                val listener = object : SensorEventListener {
                    val startTime = System.currentTimeMillis()

                    override fun onSensorChanged(event: SensorEvent) {
                        val magnitude = sqrt(
                            event.values[0] * event.values[0] +
                                event.values[1] * event.values[1] +
                                event.values[2] * event.values[2],
                        )
                        samples.add(magnitude)

                        if (System.currentTimeMillis() - startTime >= ACCEL_SAMPLE_MS) {
                            sensorManager.unregisterListener(this)
                            if (cont.isActive) cont.resume(Unit)
                        }
                    }

                    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
                }

                sensorManager.registerListener(
                    listener,
                    accel,
                    SensorManager.SENSOR_DELAY_NORMAL,
                    Handler(Looper.getMainLooper()),
                )

                cont.invokeOnCancellation {
                    sensorManager.unregisterListener(listener)
                }
            }
        }

        if (result == null || samples.size < MIN_ACCEL_SAMPLES) return null

        val mean = samples.average().toFloat()
        val variance = samples.map { (it - mean) * (it - mean) }.average().toFloat()

        // Driving heuristic: sustained vibration (variance in 0.5 - 5.0 range)
        // Walking has higher variance, stationary has near-zero variance
        return if (variance in DRIVING_VARIANCE_MIN..DRIVING_VARIANCE_MAX) {
            val confidence = ((variance - DRIVING_VARIANCE_MIN) /
                (DRIVING_VARIANCE_MAX - DRIVING_VARIANCE_MIN)).coerceIn(0.4f, 0.8f)
            ContextState(
                context = UserContext.DRIVING,
                confidence = confidence,
                source = "accelerometer",
            )
        } else {
            null
        }
    }

    // ── Sleep Detection (Time Window + Stationary) ───────────────────

    private fun checkSleep(): ContextState? {
        val now = java.util.Calendar.getInstance()
        val currentHour = now.get(java.util.Calendar.HOUR_OF_DAY)
        val currentMinute = now.get(java.util.Calendar.MINUTE)
        val currentTime = currentHour * 60 + currentMinute

        val sleepStart = prefs.getInt(KEY_SLEEP_START_HOUR, DEFAULT_SLEEP_START_HOUR) * 60 +
            prefs.getInt(KEY_SLEEP_START_MINUTE, 0)
        val sleepEnd = prefs.getInt(KEY_SLEEP_END_HOUR, DEFAULT_SLEEP_END_HOUR) * 60 +
            prefs.getInt(KEY_SLEEP_END_MINUTE, 0)

        val inSleepWindow = if (sleepStart > sleepEnd) {
            // Window crosses midnight (e.g. 23:00 - 07:00)
            currentTime >= sleepStart || currentTime < sleepEnd
        } else {
            currentTime in sleepStart until sleepEnd
        }

        return if (inSleepWindow) {
            ContextState(
                context = UserContext.SLEEPING,
                confidence = 0.7f,
                source = "time",
            )
        } else {
            null
        }
    }

    companion object {
        private const val TAG = "ContextDetector"

        const val PREFS_NAME = "jarvis_prefs"

        // Detection toggle keys
        const val KEY_DETECT_MEETING = "context_detect_meeting"
        const val KEY_DETECT_DRIVING = "context_detect_driving"
        const val KEY_DETECT_SLEEP = "context_detect_sleep"
        const val KEY_GAMING_SYNC = "context_gaming_sync"

        // Sleep schedule keys
        const val KEY_SLEEP_START_HOUR = "sleep_start_hour"
        const val KEY_SLEEP_START_MINUTE = "sleep_start_minute"
        const val KEY_SLEEP_END_HOUR = "sleep_end_hour"
        const val KEY_SLEEP_END_MINUTE = "sleep_end_minute"

        // Defaults
        const val DEFAULT_SLEEP_START_HOUR = 23
        const val DEFAULT_SLEEP_END_HOUR = 7

        // Accelerometer parameters
        private const val ACCEL_SAMPLE_MS = 10_000L // 10 seconds
        private const val MIN_ACCEL_SAMPLES = 50
        private const val DRIVING_VARIANCE_MIN = 0.5f
        private const val DRIVING_VARIANCE_MAX = 5.0f
    }
}
