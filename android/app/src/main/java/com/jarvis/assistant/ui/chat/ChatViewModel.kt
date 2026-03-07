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
        val text = beginSend(inputText, isSending) ?: return
        _errorMessage.value = null
        viewModelScope.launch {
            try {
                processor.queueCommand(text)
            } catch (e: Exception) {
                Log.e(TAG, "sendMessage failed", e)
                restoreFailedInput(inputText, text)
                _errorMessage.value = e.message ?: "Failed to submit message"
            } finally {
                isSending.value = false
            }
        }
    }

    companion object {
        private const val TAG = "ChatViewModel"
    }
}

/**
 * Atomically starts a send operation.
 *
 * Trims the draft, rejects blank input, and uses [MutableStateFlow.compareAndSet] to
 * flip [isSending] from `false` → `true` in one step so concurrent taps are
 * silently dropped without a race window.  The input is cleared only after the
 * lock is acquired, so the user never loses their draft due to a UI glitch.
 *
 * @return the trimmed message text, or `null` if the send should be skipped
 *         (blank input or another send already in flight).
 */
internal fun beginSend(
    inputText: MutableStateFlow<String>,
    isSending: MutableStateFlow<Boolean>,
): String? {
    val text = inputText.value.trim()
    if (text.isBlank() || !isSending.compareAndSet(expect = false, update = true)) {
        return null
    }
    inputText.value = ""
    return text
}

/**
 * Restores the draft text after a failed send.
 *
 * Only writes back [text] when the current input is blank — if the user has
 * already typed something new while the send was in flight, their newer draft
 * is preserved instead of being overwritten.
 */
internal fun restoreFailedInput(inputText: MutableStateFlow<String>, text: String) {
    if (inputText.value.isBlank()) {
        inputText.value = text
    }
}
