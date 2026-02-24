package com.jarvis.assistant.ui.settings

import android.app.Application
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.security.CryptoHelper
import com.jarvis.assistant.service.JarvisService
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val app: Application,
    private val crypto: CryptoHelper,
    private val apiClient: JarvisApiClient,
) : ViewModel() {

    val desktopUrl = MutableStateFlow(crypto.getBaseUrl())
    val syncIntervalSec = MutableStateFlow(30)
    val connectionStatus = MutableStateFlow("Checking...")
    val deviceId = MutableStateFlow(crypto.getDeviceId())
    val isBootstrapped = MutableStateFlow(crypto.isBootstrapped())

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
}
