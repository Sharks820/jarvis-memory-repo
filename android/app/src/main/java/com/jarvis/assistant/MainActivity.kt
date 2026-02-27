package com.jarvis.assistant

import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.fragment.app.FragmentActivity
import com.jarvis.assistant.feature.voice.VoiceEngine
import com.jarvis.assistant.security.BiometricHelper
import com.jarvis.assistant.security.CryptoHelper
import com.jarvis.assistant.service.JarvisService
import com.jarvis.assistant.ui.navigation.JarvisNavGraph
import com.jarvis.assistant.ui.theme.JarvisTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : FragmentActivity() {

    @Inject lateinit var voiceEngine: VoiceEngine
    @Inject lateinit var crypto: CryptoHelper

    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            val isAuthenticated by viewModel.isAuthenticated.collectAsState()
            val authError by viewModel.authError.collectAsState()

            JarvisTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    if (isAuthenticated) {
                        JarvisNavGraph(
                            isBootstrapped = crypto.isBootstrapped(),
                            voiceEngine = voiceEngine,
                        )
                    } else {
                        // Locked screen
                        Column(
                            modifier = Modifier.fillMaxSize(),
                            verticalArrangement = Arrangement.Center,
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Text(
                                text = "Jarvis",
                                style = MaterialTheme.typography.displayLarge,
                                color = MaterialTheme.colorScheme.primary,
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = "Authenticate to continue",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            if (authError != null) {
                                Spacer(modifier = Modifier.height(12.dp))
                                Text(
                                    text = authError!!,
                                    color = MaterialTheme.colorScheme.error,
                                    style = MaterialTheme.typography.bodyLarge,
                                )
                            }
                            Spacer(modifier = Modifier.height(24.dp))
                            Button(onClick = { promptBiometric() }) {
                                Text("Unlock")
                            }
                        }
                    }
                }
            }
        }

        // Auto-prompt biometric on launch (only if not already authenticated,
        // e.g. after rotation the ViewModel preserves the auth state)
        if (!viewModel.isAuthenticated.value) {
            if (BiometricHelper.canAuthenticate(this)) {
                promptBiometric()
            } else {
                viewModel.setAuthenticated(true)
            }
        }

        // Start the foreground sync service
        startForegroundService(Intent(this, JarvisService::class.java))

        // Handle "Talk to Jarvis" notification action
        handleVoiceIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleVoiceIntent(intent)
    }

    private fun handleVoiceIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(JarvisService.EXTRA_VOICE_COMMAND, false) == true) {
            if (viewModel.isAuthenticated.value) {
                voiceEngine.startListening()
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
    }

    private fun promptBiometric() {
        BiometricHelper.authenticate(
            activity = this,
            onSuccess = {
                viewModel.onAuthSuccess()
            },
            onError = { error ->
                viewModel.onAuthError(error)
            },
        )
    }
}
