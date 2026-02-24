package com.jarvis.assistant.ui.onboarding

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel

@Composable
fun BootstrapScreen(
    onBootstrapComplete: () -> Unit,
    viewModel: BootstrapViewModel = hiltViewModel(),
) {
    val url by viewModel.desktopUrl.collectAsState()
    val password by viewModel.masterPassword.collectAsState()
    val isConnecting by viewModel.isConnecting.collectAsState()
    val error by viewModel.error.collectAsState()
    val testResult by viewModel.testResult.collectAsState()
    var showPassword by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("Jarvis", style = MaterialTheme.typography.displayLarge)
        Spacer(Modifier.height(8.dp))
        Text(
            "Connect to your desktop brain",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(32.dp))

        // Desktop URL
        OutlinedTextField(
            value = url,
            onValueChange = { viewModel.desktopUrl.value = it },
            label = { Text("Desktop URL") },
            placeholder = { Text("http://192.168.1.x:8787") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            trailingIcon = {
                when (testResult) {
                    true -> Icon(Icons.Filled.Check, null, tint = Color(0xFF4CAF50), modifier = Modifier.size(20.dp))
                    false -> Icon(Icons.Filled.Close, null, tint = Color(0xFFF44336), modifier = Modifier.size(20.dp))
                    null -> {}
                }
            },
        )

        Spacer(Modifier.height(8.dp))

        OutlinedButton(
            onClick = { viewModel.testConnection() },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Test Connection")
        }

        Spacer(Modifier.height(16.dp))

        // Master password
        OutlinedTextField(
            value = password,
            onValueChange = { viewModel.masterPassword.value = it },
            label = { Text("Master Password") },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
            trailingIcon = {
                androidx.compose.material3.IconButton(onClick = { showPassword = !showPassword }) {
                    Text(if (showPassword) "Hide" else "Show", style = MaterialTheme.typography.labelSmall)
                }
            },
        )

        Spacer(Modifier.height(24.dp))

        // Connect button
        Button(
            onClick = { viewModel.connect(onBootstrapComplete) },
            modifier = Modifier.fillMaxWidth().height(52.dp),
            enabled = !isConnecting,
        ) {
            if (isConnecting) {
                CircularProgressIndicator(
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            } else {
                Text("Connect", style = MaterialTheme.typography.titleMedium)
            }
        }

        // Error
        error?.let { msg ->
            Spacer(Modifier.height(12.dp))
            Text(msg, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        }

        Spacer(Modifier.height(24.dp))
        Text(
            "Your desktop engine must be running with the mobile API enabled on port 8787",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
