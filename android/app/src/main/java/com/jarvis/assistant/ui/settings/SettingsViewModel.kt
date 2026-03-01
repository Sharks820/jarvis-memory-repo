package com.jarvis.assistant.ui.settings

import android.app.Application
import android.content.Context
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.HabitPatternEntity
import com.jarvis.assistant.data.entity.MedicationEntity
import com.jarvis.assistant.feature.callscreen.SpamScorer
import com.jarvis.assistant.feature.context.ContextDetector
import com.jarvis.assistant.feature.habit.BuiltInNudges
import com.jarvis.assistant.feature.habit.NudgeResponseTracker
import com.jarvis.assistant.feature.notifications.NotificationLearner
import com.jarvis.assistant.feature.commute.ParkingMemory
import com.jarvis.assistant.feature.prescription.MedicationScheduler
import com.jarvis.assistant.feature.prescription.RefillTracker
import com.jarvis.assistant.feature.scheduling.JarvisNotificationListenerService
import com.jarvis.assistant.security.CryptoHelper
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
    private val transactionDao: TransactionDao,
    private val commuteDao: CommuteDao,
    private val documentDao: DocumentDao,
    private val contactContextDao: ContactContextDao,
    private val callLogDao: CallLogDao,
    private val habitDao: HabitDao,
    private val nudgeLogDao: NudgeLogDao,
    private val nudgeResponseTracker: NudgeResponseTracker,
    private val builtInNudges: BuiltInNudges,
) : ViewModel() {

    val desktopUrl = MutableStateFlow(crypto.getBaseUrl())
    val syncIntervalSec = MutableStateFlow(30)
    val connectionStatus = MutableStateFlow("Checking...")
    val deviceId = MutableStateFlow(crypto.getDeviceId())
    val isBootstrapped = MutableStateFlow(crypto.isBootstrapped())

    // ── Shared Preferences (must be declared before first use) ───────

    private val contextPrefs by lazy {
        app.getSharedPreferences(ContextDetector.PREFS_NAME, Context.MODE_PRIVATE)
    }

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

    // ── Financial Watchdog Settings ───────────────────────────────────

    val financeMonitoringEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_FINANCE_MONITORING_ENABLED, true),
    )
    val alertUnusualAmounts = MutableStateFlow(
        contextPrefs.getBoolean(KEY_ALERT_UNUSUAL_AMOUNTS, true),
    )
    val alertNewMerchants = MutableStateFlow(
        contextPrefs.getBoolean(KEY_ALERT_NEW_MERCHANTS, true),
    )
    val weeklySummaryEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_WEEKLY_SUMMARY_ENABLED, true),
    )

    val weekTransactionCount = MutableStateFlow(0)
    val weekTotalSpend = MutableStateFlow(0.0)
    val weekAnomalyCount = MutableStateFlow(0)

    // ── Commute Intelligence Settings ─────────────────────────────────

    val homeLocation = MutableStateFlow("Not yet learned")
    val workLocation = MutableStateFlow("Not yet learned")
    val activeParking = MutableStateFlow("No parking saved")
    val carBluetoothNames = MutableStateFlow(
        contextPrefs.getString(ParkingMemory.PREF_CAR_BT_NAMES, "") ?: "",
    )
    val trafficAlertsEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_TRAFFIC_ALERTS, true),
    )
    val parkingMemoryEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_PARKING_MEMORY, true),
    )

    // ── Document Scanner Settings ─────────────────────────────────────

    val documentCount: StateFlow<Int> = documentDao.getCountFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val unsyncedDocCount = MutableStateFlow(0)

    val docAutoSync = MutableStateFlow(
        contextPrefs.getBoolean(KEY_DOC_AUTO_SYNC, true),
    )

    val docAutoCategorize = MutableStateFlow(
        contextPrefs.getBoolean(KEY_DOC_AUTO_CATEGORIZE, true),
    )

    // ── Relationship Memory Settings ────────────────────────────────

    val relationshipAlertsEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_RELATIONSHIP_ALERTS, true),
    )
    val preCallCardsEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_PRE_CALL_CARDS, true),
    )
    val postCallLoggingEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_POST_CALL_LOGGING, true),
    )
    val birthdayRemindersEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_BIRTHDAY_REMINDERS, true),
    )
    val anniversaryRemindersEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_ANNIVERSARY_REMINDERS, true),
    )
    val neglectedAlertsEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_NEGLECTED_ALERTS, true),
    )
    val neglectedThresholdDays = MutableStateFlow(
        contextPrefs.getInt(KEY_NEGLECTED_THRESHOLD_DAYS, 30),
    )

    val contactCount: StateFlow<Int> = contactContextDao.countFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val callLogCount: StateFlow<Int> = callLogDao.totalCountFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    // ── Habit Tracking Settings ──────────────────────────────────────

    val habitNudgesEnabled = MutableStateFlow(
        contextPrefs.getBoolean(KEY_HABIT_NUDGES_ENABLED, true),
    )

    val activePatternCount: StateFlow<Int> = habitDao.activeCountFlow()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val todayNudgeCount = MutableStateFlow(0)

    val detectedPatterns: StateFlow<List<HabitPatternEntity>> =
        habitDao.getAllActivePatternsFlow()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val suppressedCount = MutableStateFlow(0)

    val waterRemindersEnabled = MutableStateFlow(false)
    val screenBreakEnabled = MutableStateFlow(false)
    val sleepReminderEnabled = MutableStateFlow(false)

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
        loadWeekFinancialStats()
        loadCommuteStatus()
        loadUnsyncedDocCount()
        loadHabitStatus()
    }

    fun saveDesktopUrl() {
        val url = desktopUrl.value.trim()
        if (url.isBlank()) return
        crypto.setBaseUrl(url)
    }

    /**
     * @deprecated Sync interval is no longer configurable at runtime.
     * Time-critical tasks use a fixed 30s coroutine loop in JarvisService,
     * and all other periodic work is managed by WorkManager (15-minute minimum).
     * This function now only persists the UI value without restarting the service.
     */
    fun saveSyncInterval(seconds: Int) {
        syncIntervalSec.value = seconds
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
            } catch (e: Exception) {
                Log.w(TAG, "Failed to sync medication to desktop", e)
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

    // ── Financial Watchdog Setters ──────────────────────────────────

    fun setFinanceMonitoringEnabled(enabled: Boolean) {
        financeMonitoringEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_FINANCE_MONITORING_ENABLED, enabled).apply()
    }

    fun setAlertUnusualAmounts(enabled: Boolean) {
        alertUnusualAmounts.value = enabled
        contextPrefs.edit().putBoolean(KEY_ALERT_UNUSUAL_AMOUNTS, enabled).apply()
    }

    fun setAlertNewMerchants(enabled: Boolean) {
        alertNewMerchants.value = enabled
        contextPrefs.edit().putBoolean(KEY_ALERT_NEW_MERCHANTS, enabled).apply()
    }

    fun setWeeklySummaryEnabled(enabled: Boolean) {
        weeklySummaryEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_WEEKLY_SUMMARY_ENABLED, enabled).apply()
    }

    // ── Commute Intelligence Setters ─────────────────────────────────

    fun saveCarBluetoothNames(names: String) {
        carBluetoothNames.value = names
        contextPrefs.edit().putString(ParkingMemory.PREF_CAR_BT_NAMES, names).apply()
    }

    fun setTrafficAlerts(enabled: Boolean) {
        trafficAlertsEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_TRAFFIC_ALERTS, enabled).apply()
    }

    fun setParkingMemory(enabled: Boolean) {
        parkingMemoryEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_PARKING_MEMORY, enabled).apply()
    }

    // ── Document Scanner Setters ────────────────────────────────────

    fun setDocAutoSync(enabled: Boolean) {
        docAutoSync.value = enabled
        contextPrefs.edit().putBoolean(KEY_DOC_AUTO_SYNC, enabled).apply()
    }

    fun setDocAutoCategorize(enabled: Boolean) {
        docAutoCategorize.value = enabled
        contextPrefs.edit().putBoolean(KEY_DOC_AUTO_CATEGORIZE, enabled).apply()
    }

    // ── Relationship Memory Setters ──────────────────────────────────

    fun setRelationshipAlertsEnabled(enabled: Boolean) {
        relationshipAlertsEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_RELATIONSHIP_ALERTS, enabled).apply()
    }

    fun setPreCallCardsEnabled(enabled: Boolean) {
        preCallCardsEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_PRE_CALL_CARDS, enabled).apply()
    }

    fun setPostCallLoggingEnabled(enabled: Boolean) {
        postCallLoggingEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_POST_CALL_LOGGING, enabled).apply()
    }

    fun setBirthdayRemindersEnabled(enabled: Boolean) {
        birthdayRemindersEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_BIRTHDAY_REMINDERS, enabled).apply()
    }

    fun setAnniversaryRemindersEnabled(enabled: Boolean) {
        anniversaryRemindersEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_ANNIVERSARY_REMINDERS, enabled).apply()
    }

    fun setNeglectedAlertsEnabled(enabled: Boolean) {
        neglectedAlertsEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_NEGLECTED_ALERTS, enabled).apply()
    }

    fun setNeglectedThresholdDays(days: Int) {
        neglectedThresholdDays.value = days
        contextPrefs.edit().putInt(KEY_NEGLECTED_THRESHOLD_DAYS, days).apply()
    }

    // ── Habit Tracking Setters ───────────────────────────────────────

    fun setHabitNudgesEnabled(enabled: Boolean) {
        habitNudgesEnabled.value = enabled
        contextPrefs.edit().putBoolean(KEY_HABIT_NUDGES_ENABLED, enabled).apply()
    }

    fun setWaterRemindersEnabled(enabled: Boolean) {
        waterRemindersEnabled.value = enabled
        viewModelScope.launch {
            toggleBuiltInNudgeGroup(BuiltInNudges.LABEL_WATER, enabled)
        }
    }

    fun setScreenBreakEnabled(enabled: Boolean) {
        screenBreakEnabled.value = enabled
        viewModelScope.launch {
            toggleBuiltInNudgeGroup(BuiltInNudges.LABEL_SCREEN_BREAK, enabled)
        }
    }

    fun setSleepReminderEnabled(enabled: Boolean) {
        sleepReminderEnabled.value = enabled
        viewModelScope.launch {
            toggleBuiltInNudgeGroup(BuiltInNudges.LABEL_SLEEP, enabled)
        }
    }

    fun deactivatePattern(id: Long) {
        viewModelScope.launch {
            habitDao.deactivate(id)
        }
    }

    fun resetSuppression() {
        viewModelScope.launch {
            val suppressed = habitDao.getSuppressedPatterns()
            for (pattern in suppressed) {
                habitDao.unsuppress(pattern.id)
            }
            suppressedCount.value = 0
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
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load learning summary", e)
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
                    } catch (e: Exception) {
                        Log.w(TAG, "Failed to parse scheduled times JSON", e)
                        totalDoses += 1
                    }
                }
                todayDosesTotal.value = totalDoses
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load today medication status", e)
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
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load latest context state", e)
            }
        }
    }

    private fun loadWeekFinancialStats() {
        viewModelScope.launch {
            try {
                val dateFormat = SimpleDateFormat("yyyy-MM-dd", Locale.US)
                val today = dateFormat.format(Date())
                val cal = java.util.Calendar.getInstance()
                cal.add(java.util.Calendar.DAY_OF_YEAR, -7)
                val weekStart = dateFormat.format(cal.time)

                val transactions = transactionDao.getTransactionsInRange(weekStart, today)
                weekTransactionCount.value = transactions.size
                weekTotalSpend.value = transactionDao.getTotalSpendInRange(weekStart, today) ?: 0.0
                weekAnomalyCount.value = transactionDao.getAnomalyCountInRange(weekStart, today)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load weekly financial stats", e)
            }
        }
    }

    private fun loadUnsyncedDocCount() {
        viewModelScope.launch {
            try {
                val unsynced = documentDao.getUnsyncedDocuments()
                unsyncedDocCount.value = unsynced.size
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load unsynced document count", e)
            }
        }
    }

    private fun loadCommuteStatus() {
        viewModelScope.launch {
            try {
                val home = commuteDao.getLocationByLabel("home")
                homeLocation.value = if (home != null) {
                    "%.4f, %.4f".format(home.latitude, home.longitude)
                } else {
                    "Not yet learned"
                }

                val work = commuteDao.getLocationByLabel("work")
                workLocation.value = if (work != null) {
                    "%.4f, %.4f".format(work.latitude, work.longitude)
                } else {
                    "Not yet learned"
                }

                val parking = commuteDao.getActiveParking()
                activeParking.value = if (parking != null) {
                    val timeStr = SimpleDateFormat(
                        "HH:mm",
                        Locale.US,
                    ).format(Date(parking.timestamp))
                    "%.4f, %.4f at $timeStr".format(parking.latitude, parking.longitude)
                } else {
                    "No parking saved"
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load commute status", e)
            }
        }
    }

    private fun loadHabitStatus() {
        viewModelScope.launch {
            try {
                // Ensure built-in patterns exist
                builtInNudges.ensureBuiltInPatterns()

                // Load today's nudge count
                val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                val todayLogs = nudgeLogDao.getLogsForDate(today)
                todayNudgeCount.value = todayLogs.size

                // Count suppressed patterns
                val suppressed = habitDao.getSuppressedPatterns()
                suppressedCount.value = suppressed.size

                // Load built-in nudge toggle states
                val waterPattern = habitDao.findByTypeAndLabel("built_in", BuiltInNudges.LABEL_WATER)
                waterRemindersEnabled.value = waterPattern?.isActive == true

                val screenPattern = habitDao.findByTypeAndLabel("built_in", BuiltInNudges.LABEL_SCREEN_BREAK)
                screenBreakEnabled.value = screenPattern?.isActive == true

                val sleepPattern = habitDao.findByTypeAndLabel("built_in", BuiltInNudges.LABEL_SLEEP)
                sleepReminderEnabled.value = sleepPattern?.isActive == true
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load habit status", e)
            }
        }
    }

    /**
     * Activate or deactivate all built-in patterns whose label starts with [labelPrefix].
     * E.g. "Water Reminder" matches "Water Reminder", "Water Reminder (Afternoon)", etc.
     */
    private suspend fun toggleBuiltInNudgeGroup(labelPrefix: String, active: Boolean) {
        val allActive = habitDao.getAllActivePatterns()
        val allPatterns = allActive + habitDao.getSuppressedPatterns()
        for (pattern in allPatterns) {
            if (pattern.patternType == "built_in" && pattern.label.startsWith(labelPrefix)) {
                if (active && !pattern.isActive) {
                    habitDao.activate(pattern.id)
                } else if (!active && pattern.isActive) {
                    habitDao.deactivate(pattern.id)
                }
            }
        }
    }

    companion object {
        private const val TAG = "SettingsViewModel"
        private const val KEY_PROACTIVE_ALERTS_ENABLED = "proactive_alerts_enabled"
        private const val KEY_EMERGENCY_CONTACTS = "emergency_contacts"
        private const val KEY_FINANCE_MONITORING_ENABLED = "finance_monitoring_enabled"
        private const val KEY_ALERT_UNUSUAL_AMOUNTS = "alert_unusual_amounts"
        private const val KEY_ALERT_NEW_MERCHANTS = "alert_new_merchants"
        private const val KEY_WEEKLY_SUMMARY_ENABLED = "weekly_summary_enabled"
        private const val KEY_TRAFFIC_ALERTS = "traffic_alerts"
        private const val KEY_PARKING_MEMORY = "parking_memory"
        private const val KEY_DOC_AUTO_SYNC = "doc_auto_sync"
        private const val KEY_DOC_AUTO_CATEGORIZE = "doc_auto_categorize"
        private const val KEY_RELATIONSHIP_ALERTS = "relationship_alerts_enabled"
        private const val KEY_PRE_CALL_CARDS = "pre_call_cards_enabled"
        private const val KEY_POST_CALL_LOGGING = "post_call_logging_enabled"
        private const val KEY_BIRTHDAY_REMINDERS = "birthday_reminders_enabled"
        private const val KEY_ANNIVERSARY_REMINDERS = "anniversary_reminders_enabled"
        private const val KEY_NEGLECTED_ALERTS = "neglected_alerts_enabled"
        private const val KEY_NEGLECTED_THRESHOLD_DAYS = "neglected_threshold_days"
        private const val KEY_HABIT_NUDGES_ENABLED = "habit_nudges_enabled"
    }
}
