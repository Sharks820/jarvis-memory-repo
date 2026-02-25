package com.jarvis.assistant.feature.voice

import android.app.Application
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import com.jarvis.assistant.data.CommandQueueProcessor
import com.jarvis.assistant.data.dao.CommandQueueDao
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Full voice round-trip: push-to-talk → STT → desktop command → TTS response.
 *
 * All [SpeechRecognizer] calls happen on [Dispatchers.Main] as required by
 * the Android framework.
 */
@Singleton
class VoiceEngine @Inject constructor(
    private val app: Application,
    private val processor: CommandQueueProcessor,
    private val commandQueueDao: CommandQueueDao,
) {
    private val supervisorJob = SupervisorJob()
    private val scope = CoroutineScope(supervisorJob + Dispatchers.Main)
    private val ttsMutex = Mutex()

    private val _state = MutableStateFlow<VoiceState>(VoiceState.Idle)
    val state: StateFlow<VoiceState> = _state

    private var speechRecognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    var ttsSpeed: Float = 1.0f
        set(value) { field = value.coerceIn(0.5f, 2.0f) }

    // ── STT ────────────────────────────────────────────────────────────

    fun startListening() {
        if (_state.value !is VoiceState.Idle && _state.value !is VoiceState.Error) return

        if (!SpeechRecognizer.isRecognitionAvailable(app)) {
            _state.value = VoiceState.Error("Speech recognition not available on this device")
            return
        }

        val recognizer = SpeechRecognizer.createSpeechRecognizer(app).also {
            speechRecognizer = it
        }

        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }

        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                _state.value = VoiceState.Listening
            }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}

            override fun onPartialResults(partialResults: Bundle?) {
                val text = partialResults
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull() ?: return
                _state.value = VoiceState.Transcribing(text)
            }

            override fun onResults(results: Bundle?) {
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                if (text.isNullOrBlank()) {
                    _state.value = VoiceState.Error("Didn't catch that — please try again")
                    return
                }
                processCommand(text)
            }

            override fun onError(error: Int) {
                val msg = when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH -> "Didn't catch that — please try again"
                    SpeechRecognizer.ERROR_NETWORK,
                    SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Network error — check your connection"
                    SpeechRecognizer.ERROR_AUDIO -> "Microphone error"
                    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission required"
                    SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected"
                    else -> "Recognition error (code $error)"
                }
                _state.value = VoiceState.Error(msg)
            }

            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        recognizer.startListening(intent)
        _state.value = VoiceState.Listening
    }

    fun stopListening() {
        speechRecognizer?.stopListening()
        if (_state.value is VoiceState.Listening || _state.value is VoiceState.Transcribing) {
            _state.value = VoiceState.Idle
        }
    }

    // ── Command dispatch ───────────────────────────────────────────────

    private fun processCommand(text: String) {
        _state.value = VoiceState.Processing(text)
        scope.launch(Dispatchers.IO) {
            try {
                val id = processor.queueCommand(text, speak = true)
                // Poll for the response (desktop writes it back to Room).
                var response: String? = null
                for (i in 0 until POLL_ATTEMPTS) {
                    delay(POLL_INTERVAL_MS)
                    val cmd = commandQueueDao.getById(id)
                    if (cmd != null && cmd.status == "sent" && cmd.response != null) {
                        response = cmd.response
                        break
                    }
                    if (cmd != null && cmd.status == "failed") {
                        response = "Command failed after retries."
                        break
                    }
                }
                val reply = response
                    ?: "I've queued your command, but the desktop hasn't responded yet."
                speakResponse(reply)
            } catch (e: Exception) {
                Log.e(TAG, "processCommand error", e)
                _state.value = VoiceState.Error("Failed to send command: ${e.message}")
            }
        }
    }

    // ── TTS ────────────────────────────────────────────────────────────

    private fun speakResponse(text: String) {
        scope.launch {
            ttsMutex.withLock {
                _state.value = VoiceState.Speaking(text)
                ensureTts { engine ->
                    engine.setSpeechRate(ttsSpeed)
                    engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                        override fun onStart(utteranceId: String?) {}
                        override fun onDone(utteranceId: String?) {
                            _state.value = VoiceState.Idle
                        }
                        @Deprecated("Deprecated in Java")
                        override fun onError(utteranceId: String?) {
                            _state.value = VoiceState.Idle
                        }
                    })
                    engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis_response")
                }
            }
        }
    }

    fun cancelSpeaking() {
        tts?.stop()
        _state.value = VoiceState.Idle
    }

    private fun ensureTts(block: (TextToSpeech) -> Unit) {
        val existing = tts
        if (existing != null && ttsReady) {
            block(existing)
            return
        }
        tts = TextToSpeech(app) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.UK  // British persona
                ttsReady = true
                block(tts!!)
            } else {
                _state.value = VoiceState.Error("TTS initialisation failed")
            }
        }
    }

    fun destroy() {
        supervisorJob.cancel()
        speechRecognizer?.destroy()
        speechRecognizer = null
        tts?.shutdown()
        tts = null
        ttsReady = false
    }

    companion object {
        private const val TAG = "VoiceEngine"
        private const val POLL_INTERVAL_MS = 500L
        private const val POLL_ATTEMPTS = 60 // 30 seconds max
    }
}
