package com.jarvis.assistant.ui.chat

import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the [beginSend] and [restoreFailedInput] helpers that govern
 * the atomicity and safety of the chat message send path.
 *
 * These helpers are `internal` (package-private) pure functions, so they are
 * tested in isolation — no ViewModel lifecycle, Coroutines test rule, or Hilt
 * wiring required.
 */
class ChatViewModelHelpersTest {

    // ─────────────────────────────────────────────── beginSend ───────────────

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
    fun beginSend_clearsInputOnlyAfterLockAcquired() {
        // If the lock could not be acquired the input must remain untouched.
        val inputText = MutableStateFlow("important message")
        val isSending = MutableStateFlow(true) // already locked

        val text = beginSend(inputText, isSending)

        assertNull(text)
        assertEquals("important message", inputText.value) // draft preserved
    }

    @Test
    fun beginSend_returnsNull_whenMessageBlank() {
        val inputText = MutableStateFlow("   ")
        val isSending = MutableStateFlow(false)

        val text = beginSend(inputText, isSending)

        assertNull(text)
        assertFalse(isSending.value) // lock must NOT be acquired for blank input
    }

    @Test
    fun beginSend_returnsNull_whenSendAlreadyInProgress() {
        val inputText = MutableStateFlow("hello")
        val isSending = MutableStateFlow(true)

        val text = beginSend(inputText, isSending)

        assertNull(text)
        assertEquals("hello", inputText.value) // draft preserved
        assertTrue(isSending.value)             // lock state unchanged
    }

    @Test
    fun beginSend_trims_leadingAndTrailingWhitespace() {
        val inputText = MutableStateFlow("\t  set a reminder for noon  \n")
        val isSending = MutableStateFlow(false)

        val text = beginSend(inputText, isSending)

        assertEquals("set a reminder for noon", text)
    }

    // ────────────────────────────────────────── restoreFailedInput ───────────

    @Test
    fun restoreFailedInput_restoresClearedDraft() {
        val inputText = MutableStateFlow("")

        restoreFailedInput(inputText, "hello Jarvis")

        assertEquals("hello Jarvis", inputText.value)
    }

    @Test
    fun restoreFailedInput_preservesNewerDraft() {
        // User typed a follow-up message while the first send was in flight.
        // The recovery path must not overwrite it.
        val inputText = MutableStateFlow("new draft")

        restoreFailedInput(inputText, "old draft")

        assertEquals("new draft", inputText.value)
    }

    @Test
    fun restoreFailedInput_noOp_whenCurrentInputIsWhitespaceOnly() {
        // Whitespace is considered "blank", so the failed draft should be restored.
        val inputText = MutableStateFlow("   ")

        restoreFailedInput(inputText, "original draft")

        // isBlank() == true for whitespace, so restore should write back
        assertEquals("original draft", inputText.value)
    }
}
