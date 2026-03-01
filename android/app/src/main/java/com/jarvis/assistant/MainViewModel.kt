package com.jarvis.assistant

import androidx.lifecycle.ViewModel
import com.jarvis.assistant.security.CryptoHelper
import java.security.MessageDigest
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import javax.inject.Inject

/**
 * ViewModel for [MainActivity] that holds authentication state across
 * configuration changes (e.g. screen rotation).
 *
 * Plain Activity-level `mutableStateOf` resets on rotation because
 * the Activity is destroyed and recreated. A ViewModel survives the
 * configuration change lifecycle.
 */
@HiltViewModel
class MainViewModel @Inject constructor() : ViewModel() {

    private val _isAuthenticated = MutableStateFlow(false)
    val isAuthenticated: StateFlow<Boolean> = _isAuthenticated

    private val _authError = MutableStateFlow<String?>(null)
    val authError: StateFlow<String?> = _authError

    private val _showPasswordPrompt = MutableStateFlow(false)
    val showPasswordPrompt: StateFlow<Boolean> = _showPasswordPrompt

    private val _passwordInput = MutableStateFlow("")
    val passwordInput: StateFlow<String> = _passwordInput

    private val _noAuthWarning = MutableStateFlow<String?>(null)
    val noAuthWarning: StateFlow<String?> = _noAuthWarning

    /** Voice intent deferred until after authentication completes. */
    private val _pendingVoiceIntent = MutableStateFlow(false)
    val pendingVoiceIntent: StateFlow<Boolean> = _pendingVoiceIntent

    fun setAuthenticated(value: Boolean) {
        _isAuthenticated.value = value
    }

    fun setAuthError(error: String?) {
        _authError.value = error
    }

    fun onAuthSuccess() {
        _isAuthenticated.value = true
        _authError.value = null
        _noAuthWarning.value = null
        _showPasswordPrompt.value = false
    }

    fun onAuthError(error: String) {
        _authError.value = error
    }

    fun showPasswordPrompt(show: Boolean) {
        _showPasswordPrompt.value = show
    }

    fun onPasswordChanged(password: String) {
        _passwordInput.value = password
    }

    fun showNoAuthWarning(warning: String) {
        _noAuthWarning.value = warning
    }

    fun setPendingVoiceIntent(pending: Boolean) {
        _pendingVoiceIntent.value = pending
    }

    /** Consume pending voice intent (returns true if one was pending). */
    fun consumePendingVoiceIntent(): Boolean {
        if (_pendingVoiceIntent.value) {
            _pendingVoiceIntent.value = false
            return true
        }
        return false
    }

    /**
     * Verify the entered master password against the stored value.
     * If correct, authenticate the user.
     */
    fun verifyMasterPassword(crypto: CryptoHelper) {
        val entered = _passwordInput.value
        if (entered.isBlank()) {
            _authError.value = "Password cannot be empty"
            return
        }
        val stored = crypto.getMasterPassword()
        if (stored.isNotEmpty() && MessageDigest.isEqual(
                entered.toByteArray(Charsets.UTF_8),
                stored.toByteArray(Charsets.UTF_8),
            )
        ) {
            onAuthSuccess()
        } else {
            _authError.value = "Incorrect master password"
            _passwordInput.value = ""
        }
    }
}
