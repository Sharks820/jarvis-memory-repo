package com.jarvis.assistant

import androidx.lifecycle.ViewModel
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

    fun setAuthenticated(value: Boolean) {
        _isAuthenticated.value = value
    }

    fun setAuthError(error: String?) {
        _authError.value = error
    }

    fun onAuthSuccess() {
        _isAuthenticated.value = true
        _authError.value = null
    }

    fun onAuthError(error: String) {
        _authError.value = error
    }
}
