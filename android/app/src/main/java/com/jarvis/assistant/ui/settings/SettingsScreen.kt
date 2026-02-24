package com.jarvis.assistant.ui.settings

import android.content.Intent
import android.provider.Settings
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
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Circle
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.foundation.text.KeyboardOptions
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.assistant.feature.callscreen.createCallScreeningRoleIntent
import com.jarvis.assistant.feature.callscreen.isCallScreeningRoleGranted

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

        // ── Call Screening ────────────────────────────────────
        item {
            val context = LocalContext.current
            val callScreenEnabled by viewModel.callScreenEnabled.collectAsState()
            val blockThresh by viewModel.blockThreshold.collectAsState()
            val silenceThresh by viewModel.silenceThreshold.collectAsState()
            val voicemailThresh by viewModel.voicemailThreshold.collectAsState()
            val spamCount by viewModel.spamDbCount.collectAsState()
            var roleGranted by remember { mutableStateOf(isCallScreeningRoleGranted(context)) }
            val lifecycleOwner = LocalLifecycleOwner.current
            LaunchedEffect(lifecycleOwner) {
                lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
                    roleGranted = isCallScreeningRoleGranted(context)
                }
            }

            SectionHeader("Call Screening")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    // Enable toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enable Call Screening", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = callScreenEnabled,
                            onCheckedChange = { viewModel.setCallScreenEnabled(it) },
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    // Block threshold slider
                    Text(
                        "Block threshold: ${"%.2f".format(blockThresh)}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Slider(
                        value = blockThresh,
                        onValueChange = { viewModel.setBlockThreshold(it) },
                        valueRange = 0f..1f,
                        enabled = callScreenEnabled,
                    )

                    // Silence threshold slider
                    Text(
                        "Silence threshold: ${"%.2f".format(silenceThresh)}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Slider(
                        value = silenceThresh,
                        onValueChange = { viewModel.setSilenceThreshold(it) },
                        valueRange = 0f..1f,
                        enabled = callScreenEnabled,
                    )

                    // Voicemail threshold slider
                    Text(
                        "Voicemail threshold: ${"%.2f".format(voicemailThresh)}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Slider(
                        value = voicemailThresh,
                        onValueChange = { viewModel.setVoicemailThreshold(it) },
                        valueRange = 0f..1f,
                        enabled = callScreenEnabled,
                    )

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Spam numbers tracked: $spamCount",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    // Permission button (only shown if role not held)
                    if (!roleGranted) {
                        Spacer(Modifier.height(8.dp))
                        FilledTonalButton(
                            onClick = {
                                val intent = createCallScreeningRoleIntent(context)
                                context.startActivity(intent)
                            },
                        ) {
                            Text("Request Call Screening Permission")
                        }
                    }
                }
            }
        }

        // ── Prescriptions ─────────────────────────────────
        item {
            val activeMeds by viewModel.activeMedications.collectAsState()
            val medCount by viewModel.activeMedicationCount.collectAsState()
            val dosesTaken by viewModel.todayDosesTaken.collectAsState()
            val dosesTotal by viewModel.todayDosesTotal.collectAsState()
            var showAddDialog by remember { mutableStateOf(false) }

            SectionHeader("Prescriptions")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text(
                        "Active medications: $medCount",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Today: $dosesTaken/$dosesTotal doses taken",
                        style = MaterialTheme.typography.bodyMedium,
                    )

                    Spacer(Modifier.height(12.dp))

                    FilledTonalButton(onClick = { showAddDialog = true }) {
                        Text("Add Medication")
                    }

                    Spacer(Modifier.height(12.dp))

                    // List active medications
                    for (med in activeMeds) {
                        Card(
                            Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp),
                        ) {
                            Row(
                                Modifier
                                    .fillMaxWidth()
                                    .padding(12.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        "${med.name} - ${med.dosage}",
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                    Text(
                                        "${med.pillsRemaining} pills remaining",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = if (med.pillsRemaining <= med.refillReminderDays) {
                                            MaterialTheme.colorScheme.error
                                        } else {
                                            MaterialTheme.colorScheme.onSurfaceVariant
                                        },
                                    )
                                }
                                Switch(
                                    checked = med.isActive,
                                    onCheckedChange = { active ->
                                        if (!active) viewModel.deactivateMedication(med.id)
                                    },
                                )
                            }
                        }
                    }
                }
            }

            if (showAddDialog) {
                AddMedicationDialog(
                    onDismiss = { showAddDialog = false },
                    onSave = { name, dosage, frequency, times, pills, refillDays, notes ->
                        viewModel.addMedication(name, dosage, frequency, times, pills, refillDays, notes)
                        showAddDialog = false
                    },
                )
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

        // ── Scheduling Intelligence ───────────────────────
        item {
            val context = LocalContext.current
            val schedulingEnabled by viewModel.schedulingExtractionEnabled.collectAsState()
            val autoCreateThreshold by viewModel.schedulingAutoCreateThreshold.collectAsState()
            val eventCount by viewModel.extractedEventCount.collectAsState()
            val listenerEnabled by viewModel.notificationListenerEnabled.collectAsState()

            SectionHeader("Scheduling Intelligence")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    // Enable toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            "Extract Events from Notifications",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Switch(
                            checked = schedulingEnabled,
                            onCheckedChange = { viewModel.setSchedulingExtractionEnabled(it) },
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    // Auto-create confidence threshold slider
                    Text(
                        "Auto-create confidence threshold: ${"%.1f".format(autoCreateThreshold)}",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Slider(
                        value = autoCreateThreshold,
                        onValueChange = { viewModel.setSchedulingAutoCreateThreshold(it) },
                        valueRange = 0f..1f,
                        enabled = schedulingEnabled,
                    )
                    Text(
                        "Events below this confidence require manual confirmation",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Events extracted: $eventCount",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    // Notification access button (shown only if listener not enabled)
                    if (!listenerEnabled) {
                        Spacer(Modifier.height(8.dp))
                        FilledTonalButton(
                            onClick = {
                                val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                                context.startActivity(intent)
                                // Refresh state when user returns
                                viewModel.refreshNotificationListenerState()
                            },
                        ) {
                            Text("Enable Notification Access")
                        }
                    }
                }
            }
        }

        // ── Proactive Notifications ──────────────────────
        item {
            val proactiveEnabled by viewModel.proactiveAlertsEnabled.collectAsState()
            val notifCount by viewModel.notificationLogCount.collectAsState()
            val learningSummary by viewModel.learningSummary.collectAsState()

            SectionHeader("Proactive Notifications")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enable Proactive Alerts", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = proactiveEnabled,
                            onCheckedChange = { viewModel.setProactiveAlertsEnabled(it) },
                        )
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Notifications tracked: $notifCount",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    if (learningSummary.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Learning insights:",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        for ((alertType, dismissRate) in learningSummary) {
                            Text(
                                "  $alertType: ${"%.0f".format(dismissRate * 100)}% dismiss rate",
                                style = MaterialTheme.typography.labelSmall,
                                color = if (dismissRate > 0.8f) {
                                    MaterialTheme.colorScheme.error
                                } else {
                                    MaterialTheme.colorScheme.onSurfaceVariant
                                },
                            )
                        }
                    }

                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = { viewModel.resetLearningData() }) {
                        Text("Reset Learning Data")
                    }
                }
            }
        }

        // ── Context Awareness ────────────────────────────
        item {
            val currentContextLabel by viewModel.currentContextLabel.collectAsState()
            val currentConfidence by viewModel.currentContextConfidence.collectAsState()
            val detectMeeting by viewModel.detectMeeting.collectAsState()
            val detectDriving by viewModel.detectDriving.collectAsState()
            val detectSleep by viewModel.detectSleep.collectAsState()
            val gamingSync by viewModel.gamingSync.collectAsState()
            val sleepStartHour by viewModel.sleepStartHour.collectAsState()
            val sleepEndHour by viewModel.sleepEndHour.collectAsState()
            val emergencyContacts by viewModel.emergencyContacts.collectAsState()

            SectionHeader("Context Awareness")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text(
                        "Current: $currentContextLabel (${"%.0f".format(currentConfidence * 100)}%)",
                        style = MaterialTheme.typography.bodyMedium,
                    )

                    Spacer(Modifier.height(12.dp))

                    // Meeting detection toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Auto-detect Meeting", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = detectMeeting,
                            onCheckedChange = { viewModel.setDetectMeeting(it) },
                        )
                    }

                    // Driving detection toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Auto-detect Driving", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = detectDriving,
                            onCheckedChange = { viewModel.setDetectDriving(it) },
                        )
                    }

                    // Sleep detection toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Auto-detect Sleep", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = detectSleep,
                            onCheckedChange = { viewModel.setDetectSleep(it) },
                        )
                    }

                    // Sleep schedule
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Sleep window: ${sleepStartHour}:00 - ${sleepEndHour}:00",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text("Sleep Start Hour", style = MaterialTheme.typography.labelSmall)
                    Slider(
                        value = sleepStartHour.toFloat(),
                        onValueChange = { viewModel.setSleepStartHour(it.toInt()) },
                        valueRange = 18f..23f,
                        steps = 4,
                        enabled = detectSleep,
                    )
                    Text("Sleep End Hour", style = MaterialTheme.typography.labelSmall)
                    Slider(
                        value = sleepEndHour.toFloat(),
                        onValueChange = { viewModel.setSleepEndHour(it.toInt()) },
                        valueRange = 5f..10f,
                        steps = 4,
                        enabled = detectSleep,
                    )

                    Spacer(Modifier.height(8.dp))

                    // Gaming mode sync toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Gaming Mode Sync", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = gamingSync,
                            onCheckedChange = { viewModel.setGamingSync(it) },
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    // Emergency contacts
                    OutlinedTextField(
                        value = emergencyContacts,
                        onValueChange = { viewModel.setEmergencyContacts(it) },
                        label = { Text("Emergency Contacts") },
                        placeholder = { Text("Comma-separated phone numbers") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = false,
                        maxLines = 3,
                    )
                    Text(
                        "These contacts bypass meeting silence",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddMedicationDialog(
    onDismiss: () -> Unit,
    onSave: (
        name: String,
        dosage: String,
        frequency: String,
        times: String,
        pillsRemaining: Int,
        refillReminderDays: Int,
        notes: String,
    ) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var dosage by remember { mutableStateOf("") }
    var frequency by remember { mutableStateOf("daily") }
    var times by remember { mutableStateOf("08:00") }
    var pillsRemaining by remember { mutableStateOf("30") }
    var refillDays by remember { mutableStateOf("7") }
    var notes by remember { mutableStateOf("") }
    var frequencyExpanded by remember { mutableStateOf(false) }

    val frequencyOptions = listOf(
        "daily" to "Daily",
        "twice_daily" to "Twice Daily",
        "three_times_daily" to "Three Times Daily",
        "weekly" to "Weekly",
        "as_needed" to "As Needed",
    )

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add Medication") },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Medication Name") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = dosage,
                    onValueChange = { dosage = it },
                    label = { Text("Dosage (e.g. 500mg)") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                Spacer(Modifier.height(8.dp))

                // Frequency dropdown
                ExposedDropdownMenuBox(
                    expanded = frequencyExpanded,
                    onExpandedChange = { frequencyExpanded = !frequencyExpanded },
                ) {
                    OutlinedTextField(
                        value = frequencyOptions.firstOrNull { it.first == frequency }?.second ?: "Daily",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Frequency") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = frequencyExpanded) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .menuAnchor(),
                    )
                    ExposedDropdownMenu(
                        expanded = frequencyExpanded,
                        onDismissRequest = { frequencyExpanded = false },
                    ) {
                        frequencyOptions.forEach { (value, label) ->
                            DropdownMenuItem(
                                text = { Text(label) },
                                onClick = {
                                    frequency = value
                                    frequencyExpanded = false
                                },
                            )
                        }
                    }
                }

                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = times,
                    onValueChange = { times = it },
                    label = { Text("Scheduled Times (HH:mm, comma-separated)") },
                    placeholder = { Text("08:00, 20:00") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = pillsRemaining,
                    onValueChange = { pillsRemaining = it },
                    label = { Text("Pills Remaining") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = refillDays,
                    onValueChange = { refillDays = it },
                    label = { Text("Refill Reminder (days before empty)") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = notes,
                    onValueChange = { notes = it },
                    label = { Text("Notes (optional)") },
                    placeholder = { Text("e.g. take with food") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = false,
                    maxLines = 2,
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (name.isNotBlank()) {
                        onSave(
                            name,
                            dosage,
                            frequency,
                            times,
                            pillsRemaining.toIntOrNull() ?: 30,
                            refillDays.toIntOrNull() ?: 7,
                            notes,
                        )
                    }
                },
            ) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.padding(bottom = 4.dp),
    )
}
