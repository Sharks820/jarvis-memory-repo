package com.jarvis.assistant.feature.voice

/** Describes where the voice pipeline is in its lifecycle. */
sealed class VoiceState {
    data object Idle : VoiceState()
    data object Listening : VoiceState()
    data class Transcribing(val partialText: String) : VoiceState()
    data class Processing(val transcribedText: String) : VoiceState()
    data class Speaking(val responseText: String) : VoiceState()
    data class Error(val message: String) : VoiceState()
}
