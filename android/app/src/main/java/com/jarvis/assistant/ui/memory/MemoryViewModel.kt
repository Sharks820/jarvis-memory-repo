package com.jarvis.assistant.ui.memory

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class MemoryItem(
    val content: String,
    val branch: String = "",
    val timestamp: String = "",
)

@HiltViewModel
class MemoryViewModel @Inject constructor(
    private val apiClient: JarvisApiClient,
) : ViewModel() {

    val searchQuery = MutableStateFlow("")
    private val _results = MutableStateFlow<List<MemoryItem>>(emptyList())
    val results: StateFlow<List<MemoryItem>> = _results
    val isSearching = MutableStateFlow(false)
    val errorMessage = MutableStateFlow<String?>(null)

    fun search() {
        val query = searchQuery.value.trim()
        if (query.isBlank()) return
        isSearching.value = true
        errorMessage.value = null
        viewModelScope.launch {
            try {
                val response = apiClient.api().sendCommand(
                    CommandRequest(text = "search memory: $query"),
                )
                if (response.ok) {
                    val lines = response.stdoutTail.ifEmpty {
                        listOf(response.intent)
                    }
                    _results.value = lines
                        .filter { it.isNotBlank() }
                        .map { MemoryItem(content = it) }
                    if (_results.value.isEmpty()) {
                        errorMessage.value = "No memories found for '$query'"
                    }
                } else {
                    errorMessage.value = "Search failed"
                }
            } catch (e: Exception) {
                errorMessage.value = e.message ?: "Search error"
            } finally {
                isSearching.value = false
            }
        }
    }
}
