package com.jarvis.assistant.feature.context

import android.content.Context
import android.media.AudioManager
import android.util.Log
import com.jarvis.assistant.feature.notifications.NotificationChannelManager
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Adjusts system behaviour (ringer mode, notification filtering, voice volume)
 * based on the currently detected [UserContext].
 *
 * Context filter values are stored in SharedPreferences and read by
 * [ProactiveAlertReceiver] before posting notifications.
 *
 * Requires permissions:
 * - `MODIFY_AUDIO_SETTINGS` (normal, auto-granted) for ringer mode changes
 * - `ACCESS_NOTIFICATION_POLICY` for DND modification on some devices
 */
@Singleton
class ContextAdjuster @Inject constructor(
    @ApplicationContext private val context: Context,
    @Suppress("unused") private val channelManager: NotificationChannelManager,
) {

    private val audioManager by lazy {
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    }

    /** Saved ringer mode before Jarvis overrides it, so we can restore on NORMAL. */
    private var savedRingerMode: Int? = null

    private val prefs by lazy {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    /**
     * Apply system-wide behaviour adjustments for the detected context.
     */
    fun applyContext(state: ContextState) {
        when (state.context) {
            UserContext.MEETING -> applyMeetingMode()
            UserContext.DRIVING -> applyDrivingMode()
            UserContext.SLEEPING -> applySleepMode()
            UserContext.GAMING -> applyGamingMode()
            UserContext.NORMAL -> applyNormalMode()
        }
    }

    /**
     * Read the current notification filter from SharedPreferences.
     *
     * Possible values: "all", "urgent_only", "emergency_only", "urgent_read_aloud"
     */
    fun getCurrentFilter(): String {
        return prefs.getString(KEY_NOTIFICATION_FILTER, "all") ?: "all"
    }

    // ── Mode implementations ─────────────────────────────────────────

    /**
     * Meeting mode: full silence except emergency contacts.
     */
    private fun applyMeetingMode() {
        saveCurrentRingerMode()
        setRingerModeSafe(AudioManager.RINGER_MODE_SILENT)
        prefs.edit()
            .putString(KEY_NOTIFICATION_FILTER, "emergency_only")
            .apply()
        Log.i(TAG, "Meeting mode: full silence except emergency contacts")
    }

    /**
     * Driving mode: urgent-only read aloud, all others queued.
     * Keep ringer normal so urgent alerts can be heard.
     */
    private fun applyDrivingMode() {
        saveCurrentRingerMode()
        setRingerModeSafe(AudioManager.RINGER_MODE_NORMAL)
        prefs.edit()
            .putString(KEY_NOTIFICATION_FILTER, "urgent_read_aloud")
            .putString(KEY_VOICE_VOLUME, "loud")
            .apply()
        Log.i(TAG, "Driving mode: urgent-only read aloud, all others queued")
    }

    /**
     * Sleep mode: urgent-only notifications.
     */
    private fun applySleepMode() {
        saveCurrentRingerMode()
        setRingerModeSafe(AudioManager.RINGER_MODE_SILENT)
        prefs.edit()
            .putString(KEY_NOTIFICATION_FILTER, "urgent_only")
            .apply()
        Log.i(TAG, "Sleep mode: urgent-only notifications")
    }

    /**
     * Gaming mode: suppress non-urgent notifications.
     */
    private fun applyGamingMode() {
        prefs.edit()
            .putString(KEY_NOTIFICATION_FILTER, "urgent_only")
            .apply()
        Log.i(TAG, "Gaming mode: urgent-only notifications")
    }

    /**
     * Normal mode: all notifications, no overrides.
     */
    private fun applyNormalMode() {
        val modeToRestore = savedRingerMode ?: AudioManager.RINGER_MODE_NORMAL
        savedRingerMode = null
        setRingerModeSafe(modeToRestore)
        prefs.edit()
            .putString(KEY_NOTIFICATION_FILTER, "all")
            .remove(KEY_VOICE_VOLUME)
            .apply()
        Log.i(TAG, "Normal mode: all notifications enabled, ringer restored to $modeToRestore")
    }

    /**
     * Save the current ringer mode so it can be restored when returning to NORMAL.
     * Only saves once (if savedRingerMode is null) to preserve the original user setting.
     */
    private fun saveCurrentRingerMode() {
        if (savedRingerMode == null) {
            savedRingerMode = audioManager.ringerMode
            Log.d(TAG, "Saved user ringer mode: $savedRingerMode")
        }
    }

    /**
     * Safely set ringer mode. Catches SecurityException on devices that
     * require ACCESS_NOTIFICATION_POLICY for DND changes.
     */
    private fun setRingerModeSafe(mode: Int) {
        try {
            audioManager.ringerMode = mode
        } catch (e: SecurityException) {
            Log.w(TAG, "Cannot change ringer mode (need notification policy access): ${e.message}")
        }
    }

    companion object {
        private const val TAG = "ContextAdjuster"

        const val PREFS_NAME = "jarvis_prefs"

        /** Notification filter key, read by ProactiveAlertReceiver. */
        const val KEY_NOTIFICATION_FILTER = "context_notification_filter"

        /** Voice volume override key, read by VoiceEngine. */
        const val KEY_VOICE_VOLUME = "context_voice_volume"
    }
}
