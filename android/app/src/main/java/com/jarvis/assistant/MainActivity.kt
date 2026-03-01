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
import androidx.compose.runtime.LaunchedEffect
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

    /**
     * Check if the device requires authentication.
     *
     * Returns true if biometrics are available OR a master password is configured.
     * When neither is available, the user gets a warning but can still access
     * the app (personal device scenario).
     */
    private fun requiresAuth(): Boolean {
        if (BiometricHelper.canAuthenticate(this)) return true
        if (hasMasterPasswordConfigured()) return true
        return false
    }

    /**
     * Check if a master password has been configured via CryptoHelper.
     */
    private fun hasMasterPasswordConfigured(): Boolean {
        return crypto.getMasterPassword().isNotBlank()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            val isAuthenticated by viewModel.isAuthenticated.collectAsState()
            val authError by viewModel.authError.collectAsState()
            val showPasswordPrompt by viewModel.showPasswordPrompt.collectAsState()
            val passwordInput by viewModel.passwordInput.collectAsState()
            val noAuthWarning by viewModel.noAuthWarning.collectAsState()

            JarvisTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    if (isAuthenticated) {
                        LaunchedEffect(Unit) {
                            if (viewModel.consumePendingVoiceIntent()) {
                                voiceEngine.startListening()
                            }
                        }
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
                            authError?.let { error ->
                                Spacer(modifier = Modifier.height(12.dp))
                                Text(
                                    text = error,
                                    color = MaterialTheme.colorScheme.error,
                                    style = MaterialTheme.typography.bodyLarge,
                                )
                            }
                            noAuthWarning?.let { warning ->
                                Spacer(modifier = Modifier.height(12.dp))
                                Text(
                                    text = warning,
                                    color = MaterialTheme.colorScheme.tertiary,
                                    style = MaterialTheme.typography.bodyMedium,
                                )
                            }
                            if (showPasswordPrompt) {
                                Spacer(modifier = Modifier.height(16.dp))
                                Text(
                                    text = "Enter master password:",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurface,
                                )
                                Spacer(modifier = Modifier.height(8.dp))
                                androidx.compose.material3.OutlinedTextField(
                                    value = passwordInput,
                                    onValueChange = { viewModel.onPasswordChanged(it) },
                                    singleLine = true,
                                    visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
                                )
                                Spacer(modifier = Modifier.height(12.dp))
                                Button(onClick = {
                                    viewModel.verifyMasterPassword(crypto)
                                }) {
                                    Text("Unlock with Password")
                                }
                            } else {
                                Spacer(modifier = Modifier.height(24.dp))
                                Button(onClick = { promptBiometric() }) {
                                    Text("Unlock")
                                }
                            }
                            // When no auth is available, show a "Continue anyway" button
                            if (noAuthWarning != null) {
                                Spacer(modifier = Modifier.height(12.dp))
                                Button(onClick = { viewModel.onAuthSuccess() }) {
                                    Text("Continue without authentication")
                                }
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
            } else if (hasMasterPasswordConfigured()) {
                // Biometrics unavailable but master password is set — prompt for it
                viewModel.showPasswordPrompt(true)
            } else {
                // No biometrics and no master password — warn but allow access
                viewModel.showNoAuthWarning(
                    "No biometrics or master password configured. " +
                        "Set a master password in Settings for better security."
                )
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
            // Consume the extra so activity recreation doesn't retrigger
            intent.removeExtra(JarvisService.EXTRA_VOICE_COMMAND)
            if (viewModel.isAuthenticated.value) {
                voiceEngine.startListening()
            } else {
                viewModel.setPendingVoiceIntent(true)
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
