package com.jarvis.assistant.ui.onboarding

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.security.CryptoHelper
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.net.URI
import javax.inject.Inject

@HiltViewModel
class BootstrapViewModel @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val crypto: CryptoHelper,
) : ViewModel() {

    val desktopUrl = MutableStateFlow("https://192.168.1.100:8787")
    val masterPassword = MutableStateFlow("")
    val isConnecting = MutableStateFlow(false)
    val error = MutableStateFlow<String?>(null)
    val testResult = MutableStateFlow<Boolean?>(null)
    val httpWarning = MutableStateFlow<String?>(null)

    /**
     * Returns true if the given URL uses plain http:// on a non-local address.
     * Localhost/127.0.0.1 and private network ranges (10.x.x.x, 192.168.x.x)
     * are exempt because traffic stays on the local network.
     */
    private fun isInsecureRemoteUrl(url: String): Boolean {
        val trimmed = url.trim().lowercase()
        if (!trimmed.startsWith("http://")) return false
        return !isLocalNetworkHost(trimmed)
    }

    /**
     * Returns true if the URL points to a local/private network host.
     * Allows plain HTTP for: localhost, 127.0.0.1, 10.x.x.x, 192.168.x.x
     */
    private fun isLocalNetworkHost(url: String): Boolean {
        val host = try {
            URI(url).host?.lowercase() ?: return false
        } catch (_: Exception) {
            return false
        }
        return host == "localhost" ||
            host == "127.0.0.1" ||
            host.startsWith("10.") ||
            host.startsWith("192.168.")
    }

    fun onUrlChanged(url: String) {
        desktopUrl.value = url
        httpWarning.value = if (isInsecureRemoteUrl(url)) {
            "Warning: Using plain HTTP exposes credentials on the network. Use https:// for remote connections."
        } else {
            null
        }
    }

    fun testConnection() {
        error.value = null
        testResult.value = null
        val newUrl = desktopUrl.value.trim()
        val previousUrl = crypto.getBaseUrl()
        viewModelScope.launch {
            try {
                crypto.setBaseUrl(newUrl)
                val health = apiClient.api().health()
                testResult.value = health.status == "ok"
                if (testResult.value != true) {
                    crypto.setBaseUrl(previousUrl)
                }
            } catch (e: Exception) {
                testResult.value = false
                error.value = "Cannot reach desktop: ${e.message}"
                crypto.setBaseUrl(previousUrl)
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
        if (isInsecureRemoteUrl(url)) {
            error.value = "Cannot send credentials over plain HTTP to a non-local host. Use https://"
            return
        }
        isConnecting.value = true
        error.value = null
        viewModelScope.launch {
            try {
                crypto.setBaseUrl(url)
                val body = mapOf("device_id" to "android-app")
                val response = apiClient.api().bootstrap(password, body)
                if (response.ok && response.session != null) {
                    crypto.setToken(response.session.token)
                    crypto.setSigningKey(response.session.signingKey)
                    crypto.setDeviceId(response.session.deviceId)
                    crypto.setMasterPassword(password)
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
