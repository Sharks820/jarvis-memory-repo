package com.jarvis.assistant.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.data.CommandQueueProcessor
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.ConversationEntity
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

    fun sendMessage() {
        val text = inputText.value.trim()
        if (text.isBlank() || isSending.value) return
        inputText.value = ""
        isSending.value = true
        viewModelScope.launch {
            try {
                processor.queueCommand(text)
            } finally {
                isSending.value = false
            }
        }
    }
}
