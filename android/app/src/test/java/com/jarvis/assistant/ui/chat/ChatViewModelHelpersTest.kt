package com.jarvis.assistant.ui.chat

import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatViewModelHelpersTest {

    @Test
    fun beginSend_returnsTrimmedMessage_andLocksSending() {
        val inputText = MutableStateFlow("  hello Jarvis  ")
        val isSending = MutableStateFlow(false)

        val text = beginSend(inputText, isSending)

        assertEquals("hello Jarvis", text)
        assertEquals("", inputText.value)
        assertTrue(isSending.value)
    }

    @Test
    fun beginSend_returnsNull_whenMessageBlank() {
        val inputText = MutableStateFlow("   ")
        val isSending = MutableStateFlow(false)

        val text = beginSend(inputText, isSending)

        assertNull(text)
        assertFalse(isSending.value)
    }

    @Test
    fun beginSend_returnsNull_whenSendAlreadyInProgress() {
        val inputText = MutableStateFlow("hello")
        val isSending = MutableStateFlow(true)

        val text = beginSend(inputText, isSending)

        assertNull(text)
        assertEquals("hello", inputText.value)
    }

    @Test
    fun restoreFailedInput_restoresClearedText() {
        val inputText = MutableStateFlow("")

        restoreFailedInput(inputText, "hello Jarvis")

        assertEquals("hello Jarvis", inputText.value)
    }

    @Test
    fun restoreFailedInput_preservesNewerDraft() {
        val inputText = MutableStateFlow("new draft")

        restoreFailedInput(inputText, "old draft")

        assertEquals("new draft", inputText.value)
    }
}
