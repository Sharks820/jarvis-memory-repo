package com.jarvis.assistant.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Circle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel

@Composable
fun SettingsScreen(
    onResetBootstrap: () -> Unit,
    viewModel: SettingsViewModel = hiltViewModel(),
) {
    val url by viewModel.desktopUrl.collectAsState()
    val syncSec by viewModel.syncIntervalSec.collectAsState()
    val status by viewModel.connectionStatus.collectAsState()
    val deviceIdValue by viewModel.deviceId.collectAsState()
    var showResetDialog by remember { mutableStateOf(false) }

    if (showResetDialog) {
        AlertDialog(
            onDismissRequest = { showResetDialog = false },
            title = { Text("Reset Bootstrap?") },
            text = { Text("This will erase all credentials. You'll need to re-authenticate with the desktop engine.") },
            confirmButton = {
                TextButton(onClick = {
                    showResetDialog = false
                    viewModel.resetBootstrap()
                    onResetBootstrap()
                }) { Text("Reset") }
            },
            dismissButton = {
                TextButton(onClick = { showResetDialog = false }) { Text("Cancel") }
            },
        )
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        // ── Connection ─────────────────────────────────────
        item {
            SectionHeader("Connection")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    OutlinedTextField(
                        value = url,
                        onValueChange = { viewModel.desktopUrl.value = it },
                        label = { Text("Desktop URL") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(8.dp))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        FilledTonalButton(onClick = { viewModel.saveDesktopUrl() }) {
                            Text("Save")
                        }
                        FilledTonalButton(onClick = { viewModel.checkConnection() }) {
                            Text("Reconnect")
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.Filled.Circle,
                            contentDescription = null,
                            tint = if (status == "Connected") Color(0xFF4CAF50) else Color(0xFFF44336),
                            modifier = Modifier.padding(end = 8.dp),
                        )
                        Text(status, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }

        // ── Sync ───────────────────────────────────────────
        item {
            SectionHeader("Sync")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("Sync interval: ${syncSec}s", style = MaterialTheme.typography.bodyMedium)
                    Slider(
                        value = syncSec.toFloat(),
                        onValueChange = { viewModel.syncIntervalSec.value = it.toInt() },
                        onValueChangeFinished = { viewModel.saveSyncInterval(syncSec) },
                        valueRange = 15f..300f,
                        steps = 5,
                    )
                }
            }
        }

        // ── Security ───────────────────────────────────────
        item {
            SectionHeader("Security")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("Biometric: Enabled", style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(4.dp))
                    if (deviceIdValue.isNotBlank()) {
                        Text(
                            "Device ID: ${deviceIdValue.take(12)}...",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }
        }

        // ── About ──────────────────────────────────────────
        item {
            SectionHeader("About")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text("Jarvis Android v2.0.0", style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(onClick = { showResetDialog = true }) {
                        Text("Reset Bootstrap")
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.padding(bottom = 4.dp),
    )
}
