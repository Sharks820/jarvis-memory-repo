package com.jarvis.assistant.ui.settings

import android.app.Application
import android.content.Context
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.feature.callscreen.SpamScorer
import com.jarvis.assistant.feature.scheduling.JarvisNotificationListenerService
import com.jarvis.assistant.security.CryptoHelper
import com.jarvis.assistant.service.JarvisService
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

    init {
        checkConnection()
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
}
