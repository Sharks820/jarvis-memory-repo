package com.jarvis.assistant.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.DashboardData
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class HomeViewModel @Inject constructor(
    private val apiClient: JarvisApiClient,
) : ViewModel() {

    sealed class UiState {
        data object Loading : UiState()
        data class Success(val data: DashboardData) : UiState()
        data class Error(val message: String) : UiState()
    }

    private val _uiState = MutableStateFlow<UiState>(UiState.Loading)
    val uiState: StateFlow<UiState> = _uiState

    init {
        loadDashboard()
    }

    private val _syncMessage = MutableStateFlow<String?>(null)
    val syncMessage: StateFlow<String?> = _syncMessage

    fun syncNow() {
        viewModelScope.launch {
            try {
                _syncMessage.value = "Syncing..."
                apiClient.api().health()
                // Trigger dashboard refresh as a side effect
                loadDashboard()
                _syncMessage.value = "Sync complete"
            } catch (e: Exception) {
                _syncMessage.value = "Sync failed: ${e.message}"
            }
        }
    }

    fun loadDashboard() {
        _uiState.value = UiState.Loading
        viewModelScope.launch {
            try {
                val response = apiClient.api().getDashboard()
                val data = response.dashboard
                if (data != null) {
                    _uiState.value = UiState.Success(data)
                } else {
                    _uiState.value = UiState.Error("No dashboard data available")
                }
            } catch (e: Exception) {
                _uiState.value = UiState.Error(
                    e.message ?: "Failed to load dashboard",
                )
            }
        }
    }
}
