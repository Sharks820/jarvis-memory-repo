package com.jarvis.assistant.ui.onboarding

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.security.CryptoHelper
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class BootstrapViewModel @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val crypto: CryptoHelper,
) : ViewModel() {

    val desktopUrl = MutableStateFlow("http://192.168.1.100:8787")
    val masterPassword = MutableStateFlow("")
    val isConnecting = MutableStateFlow(false)
    val error = MutableStateFlow<String?>(null)
    val testResult = MutableStateFlow<Boolean?>(null)

    fun testConnection() {
        error.value = null
        testResult.value = null
        viewModelScope.launch {
            try {
                crypto.setBaseUrl(desktopUrl.value.trim())
                val health = apiClient.api().health()
                testResult.value = health.status == "ok"
            } catch (e: Exception) {
                testResult.value = false
                error.value = "Cannot reach desktop: ${e.message}"
            }
        }
    }

    fun connect(onSuccess: () -> Unit) {
        val url = desktopUrl.value.trim()
        val password = masterPassword.value
        if (url.isBlank() || password.isBlank()) {
            error.value = "URL and password are required"
            return
        }
        isConnecting.value = true
        error.value = null
        viewModelScope.launch {
            try {
                crypto.setBaseUrl(url)
                val body = mapOf("device_name" to "android-app")
                val response = apiClient.api().bootstrap(password, body)
                if (response.ok) {
                    crypto.setToken(response.token)
                    crypto.setSigningKey(response.signingKey)
                    crypto.setDeviceId(response.deviceId)
                    onSuccess()
                } else {
                    error.value = response.message.ifBlank { "Bootstrap rejected" }
                }
            } catch (e: Exception) {
                error.value = e.message ?: "Connection failed"
            } finally {
                isConnecting.value = false
            }
        }
    }
}
