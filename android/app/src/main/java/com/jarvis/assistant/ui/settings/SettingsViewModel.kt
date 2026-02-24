package com.jarvis.assistant.ui.settings

import android.app.Application
import android.content.Context
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.entity.MedicationEntity
import com.jarvis.assistant.feature.callscreen.SpamScorer
import com.jarvis.assistant.feature.context.ContextDetector
import com.jarvis.assistant.feature.notifications.NotificationLearner
import com.jarvis.assistant.feature.prescription.MedicationScheduler
import com.jarvis.assistant.feature.prescription.RefillTracker
import com.jarvis.assistant.feature.scheduling.JarvisNotificationListenerService
import com.jarvis.assistant.security.CryptoHelper
import com.jarvis.assistant.service.JarvisService
import com.google.gson.Gson
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val app: Application,
    private val crypto: CryptoHelper,
    private val apiClient: JarvisApiClient,
    private val spamDao: SpamDao,
    private val extractedEventDao: ExtractedEventDao,
    private val notificationLogDao: NotificationLogDao,
    private val notificationLearner: NotificationLearner,
    private val contextStateDao: ContextStateDao,
    private val medicationDao: MedicationDao,
    private val medicationLogDao: MedicationLogDao,
    private val medicationScheduler: MedicationScheduler,
    private val refillTracker: RefillTracker,
) : ViewModel() {

    val desktopUrl = MutableStateFlow(crypto.getBaseUrl())
    val syncIntervalSec = MutableStateFlow(30)
    val connectionStatus = MutableStateFlow("Checking...")
    val deviceId = MutableStateFlow(crypto.getDeviceId())
    val isBootstrapped = MutableStateFlow(crypto.isBootstrapped())

    // ── Call Screening Settings ──────────────────────────────────────

    private val callScreenPrefs by lazy {
        app.getSharedPreferences(SpamScorer.PREFS_NAME, Context.MODE_PRIVATE)
    }

    val callScreenEnabled = MutableStateFlow(
        callScreenPrefs.getBoolean(SpamScorer.KEY_ENABLED, true),
    )

    val blockThreshold = MutableStateFlow(
        callScreenPrefs.getFloat(SpamScorer.KEY_BLOCK_THRESHOLD, SpamScorer.DEFAULT_BLOCK),
    )

    val silenceThreshold = MutableStateFlow(
        callScreenPrefs.getFloat(SpamScorer.KEY_SILENCE_THRESHOLD, SpamScorer.DEFAULT_SILENCE),
    )

    val voicemailThreshold = MutableStateFlow(
        callScreenPrefs.getFloat(SpamScorer.KEY_VOICEMAIL_THRESHOLD, SpamScorer.DEFAULT_VOICEMAIL),
    )

    /** Number of spam entries currently tracked in the local database. */
    val spamDbCount: StateFlow<Int> = spamDao.getAllFlow()
        .map { it.size }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    // ── Prescription / Medication Settings ───────────────────────────

    private val gson = Gson()

    val activeMedications: StateFlow<List<MedicationEntity>> =
        medicationDao.getActiveMedicationsFlow()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val activeMedicationCount: StateFlow<Int> = activeMedications
        .map { it.size }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val todayDosesTaken = MutableStateFlow(0)
    val todayDosesTotal = MutableStateFlow(0)

    // ── Scheduling Settings ──────────────────────────────────────────

    private val schedulingPrefs by lazy {
        app.getSharedPreferences(
            JarvisNotificationListenerService.PREFS_NAME,
            Context.MODE_PRIVATE,
        )
    }

    val schedulingExtractionEnabled = MutableStateFlow(
        schedulingPrefs.getBoolean(
            JarvisNotificationListenerService.KEY_EXTRACTION_ENABLED,
            true,
        ),
    )

    val schedulingAutoCreateThreshold = MutableStateFlow(
        schedulingPrefs.getFloat(
            JarvisNotificationListenerService.KEY_AUTO_CREATE_THRESHOLD,
            JarvisNotificationListenerService.DEFAULT_AUTO_CREATE_THRESHOLD,
        ),
    )

    /** Number of events extracted from notifications. */
    val extractedEventCount: StateFlow<Int> = extractedEventDao.countFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    /** Whether the notification listener is currently enabled by the user. */
    val notificationListenerEnabled = MutableStateFlow(isNotificationListenerEnabled())

    // ── Proactive Notification Settings ──────────────────────────────

    private val contextPrefs by lazy {
        app.getSharedPreferences(ContextDetector.PREFS_NAME, Context.MODE_PRIVATE)
    }

    val proactiveAlertsEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_PROACTIVE_ALERTS_ENABLED, true),
    )

    /** Total number of notification interactions tracked for learning. */
    val notificationLogCount: StateFlow<Int> = notificationLogDao.totalCountFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    /** Dismiss rate per alert type from NotificationLearner. */
    val learningSummary = MutableStateFlow<Map<String, Float>>(emptyMap())

    // ── Context Awareness Settings ───────────────────────────────────

    val currentContextLabel = MutableStateFlow("Normal")
    val currentContextConfidence = MutableStateFlow(1.0f)

    val detectMeeting = MutableStateFlow(
        contextPrefs.getBoolean(ContextDetector.KEY_DETECT_MEETING, true),
    )
    val detectDriving = MutableStateFlow(
        contextPrefs.getBoolean(ContextDetector.KEY_DETECT_DRIVING, true),
    )
    val detectSleep = MutableStateFlow(
        contextPrefs.getBoolean(ContextDetector.KEY_DETECT_SLEEP, true),
    )
    val gamingSync = MutableStateFlow(
        contextPrefs.getBoolean(ContextDetector.KEY_GAMING_SYNC, true),
    )
    val sleepStartHour = MutableStateFlow(
        contextPrefs.getInt(ContextDetector.KEY_SLEEP_START_HOUR, ContextDetector.DEFAULT_SLEEP_START_HOUR),
    )
    val sleepEndHour = MutableStateFlow(
        contextPrefs.getInt(ContextDetector.KEY_SLEEP_END_HOUR, ContextDetector.DEFAULT_SLEEP_END_HOUR),
    )
    val emergencyContacts = MutableStateFlow(
        contextPrefs.getString(KEY_EMERGENCY_CONTACTS, "") ?: "",
    )

    init {
        checkConnection()
        loadLearningSummary()
        loadLatestContext()
        loadTodayMedicationStatus()
    }

    fun saveDesktopUrl() {
        val url = desktopUrl.value.trim()
        if (url.isBlank()) return
        crypto.setBaseUrl(url)
    }

    fun saveSyncInterval(seconds: Int) {
        syncIntervalSec.value = seconds
        val intent = Intent(app, JarvisService::class.java).apply {
            putExtra(JarvisService.EXTRA_SYNC_INTERVAL, seconds * 1000L)
        }
        app.startForegroundService(intent)
    }

    fun checkConnection() {
        connectionStatus.value = "Checking..."
        viewModelScope.launch {
            try {
                val health = apiClient.api().health()
                connectionStatus.value = if (health.status == "ok") "Connected" else "Unknown status"
            } catch (e: Exception) {
                connectionStatus.value = "Offline"
            }
        }
    }

    fun resetBootstrap() {
        crypto.clearAll()
        isBootstrapped.value = false
    }

    // ── Call Screening Setters ───────────────────────────────────────

    fun setCallScreenEnabled(enabled: Boolean) {
        callScreenEnabled.value = enabled
        callScreenPrefs.edit().putBoolean(SpamScorer.KEY_ENABLED, enabled).apply()
    }

    fun setBlockThreshold(value: Float) {
        blockThreshold.value = value
        callScreenPrefs.edit().putFloat(SpamScorer.KEY_BLOCK_THRESHOLD, value).apply()
    }

    fun setSilenceThreshold(value: Float) {
        silenceThreshold.value = value
        callScreenPrefs.edit().putFloat(SpamScorer.KEY_SILENCE_THRESHOLD, value).apply()
    }

    fun setVoicemailThreshold(value: Float) {
        voicemailThreshold.value = value
        callScreenPrefs.edit().putFloat(SpamScorer.KEY_VOICEMAIL_THRESHOLD, value).apply()
    }

    // ── Scheduling Setters ───────────────────────────────────────────

    fun setSchedulingExtractionEnabled(enabled: Boolean) {
        schedulingExtractionEnabled.value = enabled
        schedulingPrefs.edit()
            .putBoolean(JarvisNotificationListenerService.KEY_EXTRACTION_ENABLED, enabled)
            .apply()
    }

    fun setSchedulingAutoCreateThreshold(value: Float) {
        schedulingAutoCreateThreshold.value = value
        schedulingPrefs.edit()
            .putFloat(JarvisNotificationListenerService.KEY_AUTO_CREATE_THRESHOLD, value)
            .apply()
    }

    /** Refresh the notification listener enabled state (call after returning from settings). */
    fun refreshNotificationListenerState() {
        notificationListenerEnabled.value = isNotificationListenerEnabled()
    }

    // ── Proactive Notification Setters ───────────────────────────────

    fun setProactiveAlertsEnabled(enabled: Boolean) {
        proactiveAlertsEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_PROACTIVE_ALERTS_ENABLED, enabled).apply()
    }

    fun resetLearningData() {
        viewModelScope.launch {
            notificationLearner.resetLearningData()
            learningSummary.value = emptyMap()
        }
    }

    // ── Context Awareness Setters ────────────────────────────────────

    fun setDetectMeeting(enabled: Boolean) {
        detectMeeting.value = enabled
        contextPrefs.edit().putBoolean(ContextDetector.KEY_DETECT_MEETING, enabled).apply()
    }

    fun setDetectDriving(enabled: Boolean) {
        detectDriving.value = enabled
        contextPrefs.edit().putBoolean(ContextDetector.KEY_DETECT_DRIVING, enabled).apply()
    }

    fun setDetectSleep(enabled: Boolean) {
        detectSleep.value = enabled
        contextPrefs.edit().putBoolean(ContextDetector.KEY_DETECT_SLEEP, enabled).apply()
    }

    fun setGamingSync(enabled: Boolean) {
        gamingSync.value = enabled
        contextPrefs.edit().putBoolean(ContextDetector.KEY_GAMING_SYNC, enabled).apply()
    }

    fun setSleepStartHour(hour: Int) {
        sleepStartHour.value = hour
        contextPrefs.edit().putInt(ContextDetector.KEY_SLEEP_START_HOUR, hour).apply()
    }

    fun setSleepEndHour(hour: Int) {
        sleepEndHour.value = hour
        contextPrefs.edit().putInt(ContextDetector.KEY_SLEEP_END_HOUR, hour).apply()
    }

    fun setEmergencyContacts(contacts: String) {
        emergencyContacts.value = contacts
        contextPrefs.edit().putString(KEY_EMERGENCY_CONTACTS, contacts).apply()
    }

    // ── Prescription / Medication Actions ────────────────────────────

    /**
     * Add a new medication and schedule its alarms.
     * [times] is a comma-separated string of HH:mm values (e.g. "08:00, 20:00").
     */
    fun addMedication(
        name: String,
        dosage: String,
        frequency: String,
        times: String,
        pillsRemaining: Int,
        refillReminderDays: Int,
        notes: String,
    ) {
        viewModelScope.launch {
            val timesList = times.split(",").map { it.trim() }.filter { it.isNotBlank() }
            val timesJson = gson.toJson(timesList)
            val entity = MedicationEntity(
                name = name,
                dosage = dosage,
                frequency = frequency,
                scheduledTimes = timesJson,
                pillsRemaining = pillsRemaining,
                refillReminderDays = refillReminderDays,
                notes = notes,
            )
            val id = medicationDao.insert(entity)
            medicationScheduler.rescheduleForMedication(id)
            try {
                refillTracker.syncToDesktop(apiClient)
            } catch (_: Exception) {
                // Desktop sync is best-effort
            }
            loadTodayMedicationStatus()
        }
    }

    /** Deactivate a medication and cancel its alarms. */
    fun deactivateMedication(id: Long) {
        viewModelScope.launch {
            medicationDao.deactivate(id)
            medicationScheduler.rescheduleForMedication(id)
            loadTodayMedicationStatus()
        }
    }

    // ── Private helpers ──────────────────────────────────────────────

    private fun isNotificationListenerEnabled(): Boolean {
        val enabledPackages = androidx.core.app.NotificationManagerCompat
            .getEnabledListenerPackages(app)
        return enabledPackages.contains(app.packageName)
    }

    private fun loadLearningSummary() {
        viewModelScope.launch {
            try {
                learningSummary.value = notificationLearner.getLearningSummary()
            } catch (_: Exception) {
                // Silently fail -- no learning data yet
            }
        }
    }

    private fun loadTodayMedicationStatus() {
        viewModelScope.launch {
            try {
                val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                val takenLogs = medicationLogDao.getTakenLogsForDate(today)
                todayDosesTaken.value = takenLogs.size

                val activeMeds = medicationDao.getActiveMedications()
                var totalDoses = 0
                for (med in activeMeds) {
                    try {
                        val type = object : com.google.gson.reflect.TypeToken<List<String>>() {}.type
                        val times: List<String> = gson.fromJson(med.scheduledTimes, type) ?: emptyList()
                        totalDoses += times.size
                    } catch (_: Exception) {
                        totalDoses += 1
                    }
                }
                todayDosesTotal.value = totalDoses
            } catch (_: Exception) {
                // No medication data yet
            }
        }
    }

    private fun loadLatestContext() {
        viewModelScope.launch {
            try {
                val latest = contextStateDao.getLatest()
                if (latest != null) {
                    currentContextLabel.value = latest.context
                    currentContextConfidence.value = latest.confidence
                }
            } catch (_: Exception) {
                // No context data yet
            }
        }
    }

    companion object {
        private const val KEY_PROACTIVE_ALERTS_ENABLED = "proactive_alerts_enabled"
        private const val KEY_EMERGENCY_CONTACTS = "emergency_contacts"
    }
}
