package com.jarvis.assistant.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.data.CommandQueueProcessor
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.ConversationEntity
import android.util.Log
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class ChatViewModel @Inject constructor(
    private val processor: CommandQueueProcessor,
    conversationDao: ConversationDao,
) : ViewModel() {

    val messages: StateFlow<List<ConversationEntity>> =
        conversationDao.getMessages()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val inputText = MutableStateFlow("")
    val isSending = MutableStateFlow(false)

    private val _errorMessage = MutableStateFlow<String?>(null)
    val errorMessage: StateFlow<String?> = _errorMessage

    fun clearError() {
        _errorMessage.value = null
    }

    fun sendMessage() {
        val text = inputText.value.trim()
        if (text.isBlank() || isSending.value) return
        inputText.value = ""
        isSending.value = true
        _errorMessage.value = null
        viewModelScope.launch {
            try {
                processor.queueCommand(text)
            } catch (e: Exception) {
                Log.e(TAG, "sendMessage failed", e)
                _errorMessage.value = e.message ?: "Failed to send command"
            } finally {
                isSending.value = false
            }
        }
    }

    companion object {
        private const val TAG = "ChatViewModel"
    }
}
