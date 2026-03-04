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
import kotlin.math.roundToInt
import androidx.hilt.navigation.compose.hiltViewModel
import android.os.Build
import com.jarvis.assistant.feature.callscreen.createCallScreeningRoleIntent
import com.jarvis.assistant.feature.callscreen.isCallScreeningRoleGranted

@Composable
fun SettingsScreen(
    onResetBootstrap: () -> Unit,
    onNavigateToDocumentScanner: () -> Unit = {},
    onNavigateToDocumentList: () -> Unit = {},
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
                        onValueChange = { viewModel.syncIntervalSec.value = it.roundToInt() },
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

                    // Permission button (only shown if role not held and API >= Q)
                    if (!roleGranted && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        Spacer(Modifier.height(8.dp))
                        FilledTonalButton(
                            onClick = {
                                val intent = createCallScreeningRoleIntent(context)
                                if (intent != null) {
                                    context.startActivity(intent)
                                }
                            },
                        ) {
                            Text("Request Call Screening Permission")
                        }
                    }
                }
            }
        }

        // ── Scam Campaign Hunter ──────────────────────────
        item {
            SectionHeader("Scam Campaign Hunter")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    val scamStats by viewModel.scamStats.collectAsState()
                    val scamCampaigns by viewModel.scamCampaigns.collectAsState()
                    val scamLoading by viewModel.scamLoading.collectAsState()

                    Text(
                        "Detects scam call centers that rotate numbers",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )

                    Spacer(Modifier.height(8.dp))

                    // Stats row
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column {
                            Text("${scamStats.totalScreened}", style = MaterialTheme.typography.titleLarge)
                            Text("Screened", style = MaterialTheme.typography.bodySmall)
                        }
                        Column {
                            Text("${scamStats.activeCampaigns}", style = MaterialTheme.typography.titleLarge)
                            Text("Campaigns", style = MaterialTheme.typography.bodySmall)
                        }
                        Column {
                            Text("${scamStats.numbersBlocked}", style = MaterialTheme.typography.titleLarge)
                            Text("Blocked", style = MaterialTheme.typography.bodySmall)
                        }
                        Column {
                            Text("${scamStats.stirFailed}", style = MaterialTheme.typography.titleLarge)
                            Text("Spoofed", style = MaterialTheme.typography.bodySmall)
                        }
                    }

                    if (scamCampaigns.isNotEmpty()) {
                        Spacer(Modifier.height(12.dp))
                        Text(
                            "Active Campaigns",
                            style = MaterialTheme.typography.titleSmall,
                        )

                        for (campaign in scamCampaigns.take(5)) {
                            Spacer(Modifier.height(4.dp))
                            Card(
                                Modifier.fillMaxWidth().padding(vertical = 2.dp),
                            ) {
                                Column(Modifier.padding(8.dp)) {
                                    Row(
                                        Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                    ) {
                                        Text(
                                            "Prefix ${campaign.prefix}",
                                            style = MaterialTheme.typography.bodyMedium,
                                        )
                                        Text(
                                            "${(campaign.confidence * 100).toInt()}% confidence",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = if (campaign.confidence >= 0.75f) {
                                                MaterialTheme.colorScheme.error
                                            } else {
                                                MaterialTheme.colorScheme.onSurfaceVariant
                                            },
                                        )
                                    }
                                    Text(
                                        "${campaign.numbers.size} numbers | ${campaign.totalCalls} calls",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    if (campaign.signals.isNotEmpty()) {
                                        Text(
                                            campaign.signals.joinToString(", "),
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.error,
                                        )
                                    }
                                    if (campaign.carrier.isNotBlank()) {
                                        Text(
                                            "Carrier: ${campaign.carrier} (${campaign.lineType})",
                                            style = MaterialTheme.typography.bodySmall,
                                        )
                                    }
                                }
                            }
                        }
                    }

                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilledTonalButton(
                            onClick = { viewModel.loadScamIntel() },
                            enabled = !scamLoading,
                        ) {
                            Text(if (scamLoading) "Loading..." else "Refresh Intel")
                        }
                    }

                    if (scamStats.topScamPrefixes.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        Text("Top Scam Prefixes", style = MaterialTheme.typography.titleSmall)
                        for (prefix in scamStats.topScamPrefixes) {
                            Text(
                                "${prefix.prefix}: ${prefix.numbers} numbers",
                                style = MaterialTheme.typography.bodySmall,
                            )
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
                                    // Compute days remaining the same way RefillTracker does:
                                    // parse scheduledTimes JSON to get doses/day, then divide.
                                    val dosesPerDay = try {
                                        val times = com.google.gson.Gson().fromJson<List<String>>(
                                            med.scheduledTimes,
                                            object : com.google.gson.reflect.TypeToken<List<String>>() {}.type,
                                        ) ?: emptyList()
                                        times.size.coerceAtLeast(1)
                                    } catch (_: Exception) { 1 }
                                    // Ceiling division to include partial days
                                    val daysRemaining = (med.pillsRemaining + dosesPerDay - 1) / dosesPerDay

                                    Text(
                                        "${med.pillsRemaining} pills remaining (~$daysRemaining days)",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = if (daysRemaining <= med.refillReminderDays) {
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
                        onValueChange = { viewModel.setSleepStartHour(it.roundToInt()) },
                        valueRange = 18f..23f,
                        steps = 4,
                        enabled = detectSleep,
                    )
                    Text("Sleep End Hour", style = MaterialTheme.typography.labelSmall)
                    Slider(
                        value = sleepEndHour.toFloat(),
                        onValueChange = { viewModel.setSleepEndHour(it.roundToInt()) },
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

        // ── Financial Watchdog ────────────────────────────
        item {
            val financeEnabled by viewModel.financeMonitoringEnabled.collectAsState()
            val weekTxCount by viewModel.weekTransactionCount.collectAsState()
            val weekSpend by viewModel.weekTotalSpend.collectAsState()
            val anomalyCount by viewModel.weekAnomalyCount.collectAsState()
            val alertUnusual by viewModel.alertUnusualAmounts.collectAsState()
            val alertNew by viewModel.alertNewMerchants.collectAsState()
            val weeklySummary by viewModel.weeklySummaryEnabled.collectAsState()

            SectionHeader("Financial Watchdog")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enable Financial Monitoring", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = financeEnabled,
                            onCheckedChange = { viewModel.setFinanceMonitoringEnabled(it) },
                        )
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Transactions this week: $weekTxCount",
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Text(
                        "Week spend: ${"$%.2f".format(weekSpend)}",
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Text(
                        "Anomalies detected: $anomalyCount",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    Spacer(Modifier.height(12.dp))

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Alert on unusual amounts", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = alertUnusual,
                            onCheckedChange = { viewModel.setAlertUnusualAmounts(it) },
                            enabled = financeEnabled,
                        )
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Alert on new merchants", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = alertNew,
                            onCheckedChange = { viewModel.setAlertNewMerchants(it) },
                            enabled = financeEnabled,
                        )
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Weekly spend summary", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = weeklySummary,
                            onCheckedChange = { viewModel.setWeeklySummaryEnabled(it) },
                            enabled = financeEnabled,
                        )
                    }
                }
            }
        }

        // ── Commute Intelligence ─────────────────────────
        item {
            val homeLocValue by viewModel.homeLocation.collectAsState()
            val workLocValue by viewModel.workLocation.collectAsState()
            val carBtNames by viewModel.carBluetoothNames.collectAsState()
            val parkingValue by viewModel.activeParking.collectAsState()
            val trafficEnabled by viewModel.trafficAlertsEnabled.collectAsState()
            val parkingEnabled by viewModel.parkingMemoryEnabled.collectAsState()

            SectionHeader("Commute Intelligence")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text(
                        "Home: $homeLocValue",
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Text(
                        "Work: $workLocValue",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    Spacer(Modifier.height(12.dp))

                    OutlinedTextField(
                        value = carBtNames,
                        onValueChange = { viewModel.saveCarBluetoothNames(it) },
                        label = { Text("Car Bluetooth Name(s)") },
                        placeholder = { Text("Comma-separated, e.g. My Car, BMW iDrive") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Current parking: $parkingValue",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    Spacer(Modifier.height(12.dp))

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Traffic alerts", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = trafficEnabled,
                            onCheckedChange = { viewModel.setTrafficAlerts(it) },
                        )
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Parking memory", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = parkingEnabled,
                            onCheckedChange = { viewModel.setParkingMemory(it) },
                        )
                    }
                }
            }
        }

        // ── Document Scanner ─────────────────────────────────
        item {
            val docCount by viewModel.documentCount.collectAsState()
            val unsyncedDocCount by viewModel.unsyncedDocCount.collectAsState()
            val docAutoSync by viewModel.docAutoSync.collectAsState()
            val docAutoCategorize by viewModel.docAutoCategorize.collectAsState()

            SectionHeader("Document Scanner")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    Text(
                        "Documents scanned: $docCount",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Pending sync: $unsyncedDocCount",
                        style = MaterialTheme.typography.bodyMedium,
                    )

                    Spacer(Modifier.height(12.dp))

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Auto-sync to desktop", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = docAutoSync,
                            onCheckedChange = { viewModel.setDocAutoSync(it) },
                        )
                    }

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Auto-categorize", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = docAutoCategorize,
                            onCheckedChange = { viewModel.setDocAutoCategorize(it) },
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    FilledTonalButton(onClick = onNavigateToDocumentScanner) {
                        Text("Scan Document")
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = onNavigateToDocumentList) {
                        Text("View All Documents")
                    }
                }
            }
        }

        // ── Relationship Memory ────────────────────────────
        item {
            val relationshipEnabled by viewModel.relationshipAlertsEnabled.collectAsState()
            val preCallCards by viewModel.preCallCardsEnabled.collectAsState()
            val postCallLogging by viewModel.postCallLoggingEnabled.collectAsState()
            val contactCount by viewModel.contactCount.collectAsState()
            val callLogCount by viewModel.callLogCount.collectAsState()
            val birthdayReminders by viewModel.birthdayRemindersEnabled.collectAsState()
            val anniversaryReminders by viewModel.anniversaryRemindersEnabled.collectAsState()
            val neglectedAlerts by viewModel.neglectedAlertsEnabled.collectAsState()
            val neglectedDays by viewModel.neglectedThresholdDays.collectAsState()

            SectionHeader("Relationship Memory")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    // Master toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enable Relationship Alerts", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = relationshipEnabled,
                            onCheckedChange = { viewModel.setRelationshipAlertsEnabled(it) },
                        )
                    }

                    // Pre-call cards toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Pre-call Context Cards", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = preCallCards,
                            onCheckedChange = { viewModel.setPreCallCardsEnabled(it) },
                            enabled = relationshipEnabled,
                        )
                    }

                    // Post-call logging toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Post-call Logging Prompts", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = postCallLogging,
                            onCheckedChange = { viewModel.setPostCallLoggingEnabled(it) },
                            enabled = relationshipEnabled,
                        )
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Contacts tracked: $contactCount",
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Text(
                        "Calls logged: $callLogCount",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    Spacer(Modifier.height(12.dp))
                    Text("Alert Settings", style = MaterialTheme.typography.bodyMedium)

                    // Birthday reminders toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Birthday Reminders", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = birthdayReminders,
                            onCheckedChange = { viewModel.setBirthdayRemindersEnabled(it) },
                            enabled = relationshipEnabled,
                        )
                    }

                    // Anniversary reminders toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Anniversary Reminders", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = anniversaryReminders,
                            onCheckedChange = { viewModel.setAnniversaryRemindersEnabled(it) },
                            enabled = relationshipEnabled,
                        )
                    }

                    // Neglected connection alerts toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Neglected Connection Alerts", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = neglectedAlerts,
                            onCheckedChange = { viewModel.setNeglectedAlertsEnabled(it) },
                            enabled = relationshipEnabled,
                        )
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Neglected threshold: $neglectedDays days",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Slider(
                        value = neglectedDays.toFloat(),
                        onValueChange = { viewModel.setNeglectedThresholdDays(it.roundToInt()) },
                        valueRange = 14f..90f,
                        steps = 14,
                        enabled = relationshipEnabled && neglectedAlerts,
                    )
                    Text(
                        "Alert when you haven't spoken with important contacts in this many days",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        // ── Habit Tracking ────────────────────────────────
        item {
            val habitEnabled by viewModel.habitNudgesEnabled.collectAsState()
            val patternCount by viewModel.activePatternCount.collectAsState()
            val todayNudges by viewModel.todayNudgeCount.collectAsState()
            val waterEnabled by viewModel.waterRemindersEnabled.collectAsState()
            val screenBreakOn by viewModel.screenBreakEnabled.collectAsState()
            val sleepOn by viewModel.sleepReminderEnabled.collectAsState()
            val patterns by viewModel.detectedPatterns.collectAsState()
            val suppressed by viewModel.suppressedCount.collectAsState()

            SectionHeader("Habit Tracking")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    // Master toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enable Habit Nudges", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = habitEnabled,
                            onCheckedChange = { viewModel.setHabitNudgesEnabled(it) },
                        )
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Patterns detected: $patternCount",
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Text(
                        "Nudges delivered today: $todayNudges",
                        style = MaterialTheme.typography.labelMedium,
                    )

                    Spacer(Modifier.height(12.dp))
                    Text("Built-in Nudges", style = MaterialTheme.typography.bodyMedium)

                    // Water reminders toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Water Reminders", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = waterEnabled,
                            onCheckedChange = { viewModel.setWaterRemindersEnabled(it) },
                            enabled = habitEnabled,
                        )
                    }

                    // Screen break reminders toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Screen Break Reminders", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = screenBreakOn,
                            onCheckedChange = { viewModel.setScreenBreakEnabled(it) },
                            enabled = habitEnabled,
                        )
                    }

                    // Sleep reminders toggle
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Sleep Reminders", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = sleepOn,
                            onCheckedChange = { viewModel.setSleepReminderEnabled(it) },
                            enabled = habitEnabled,
                        )
                    }

                    // Detected patterns list (non-built-in, active)
                    val detectedOnly = patterns.filter { it.patternType != "built_in" }
                    if (detectedOnly.isNotEmpty()) {
                        Spacer(Modifier.height(12.dp))
                        Text("Detected Patterns", style = MaterialTheme.typography.bodyMedium)

                        for (pattern in detectedOnly) {
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
                                            pattern.label,
                                            style = MaterialTheme.typography.bodyMedium,
                                        )
                                        Text(
                                            pattern.description,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                        Text(
                                            "Confidence: ${"%.0f".format(pattern.confidence * 100)}%",
                                            style = MaterialTheme.typography.labelSmall,
                                        )
                                    }
                                    Switch(
                                        checked = pattern.isActive,
                                        onCheckedChange = { active ->
                                            if (!active) viewModel.deactivatePattern(pattern.id)
                                        },
                                    )
                                }
                            }
                        }
                    }

                    // Suppression info
                    if (suppressed > 0) {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "$suppressed patterns auto-suppressed due to low engagement",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(4.dp))
                        OutlinedButton(onClick = { viewModel.resetSuppression() }) {
                            Text("Reset Suppression")
                        }
                    }
                }
            }
        }

        // ── Mute Jarvis ──────────────────────────────────────
        item {
            SectionHeader("Mute Jarvis")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    val muteActive by viewModel.muteActive.collectAsState()

                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            if (muteActive) "Jarvis is muted" else "Jarvis is active",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Switch(
                            checked = muteActive,
                            onCheckedChange = { viewModel.setMuted(it) },
                        )
                    }

                    if (!muteActive) {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Mute with timer:",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Spacer(Modifier.height(4.dp))
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            FilledTonalButton(onClick = { viewModel.muteFor(30) }) {
                                Text("30m")
                            }
                            FilledTonalButton(onClick = { viewModel.muteFor(60) }) {
                                Text("1h")
                            }
                            FilledTonalButton(onClick = { viewModel.muteFor(120) }) {
                                Text("2h")
                            }
                        }
                    }
                }
            }
        }

        // ── Automation ──────────────────────────────────────
        item {
            SectionHeader("Automation")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    val smartReplyEnabled by viewModel.smartReplyEnabled.collectAsState()
                    val smartReplySend by viewModel.smartReplySendSms.collectAsState()
                    val contextDigestEnabled by viewModel.contextDigestEnabled.collectAsState()
                    val meetingPrepEnabled by viewModel.meetingPrepEnabled.collectAsState()
                    val relationshipAutopilotEnabled by viewModel.relationshipAutopilotEnabled.collectAsState()
                    val morningBriefingEnabled by viewModel.morningBriefingEnabled.collectAsState()
                    val morningBriefingSpeak by viewModel.morningBriefingSpeak.collectAsState()

                    Text(
                        "Smart Missed-Call Reply",
                        style = MaterialTheme.typography.titleSmall,
                    )
                    Text(
                        "Auto-send SMS when you miss a call in meeting/driving/sleep",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enabled", style = MaterialTheme.typography.bodySmall)
                        Switch(checked = smartReplyEnabled, onCheckedChange = { viewModel.setSmartReplyEnabled(it) })
                    }
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Actually send SMS (not just draft)", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = smartReplySend,
                            onCheckedChange = { viewModel.setSmartReplySendSms(it) },
                            enabled = smartReplyEnabled,
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    Text("Context Digest", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Summarize what you missed after leaving meeting/drive",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enabled", style = MaterialTheme.typography.bodySmall)
                        Switch(checked = contextDigestEnabled, onCheckedChange = { viewModel.setContextDigestEnabled(it) })
                    }

                    Spacer(Modifier.height(12.dp))

                    Text("Pre-Meeting Intelligence", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "KG-powered briefing 10min before calendar events",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enabled", style = MaterialTheme.typography.bodySmall)
                        Switch(checked = meetingPrepEnabled, onCheckedChange = { viewModel.setMeetingPrepEnabled(it) })
                    }

                    Spacer(Modifier.height(12.dp))

                    Text("Relationship Autopilot", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Nudge when important contacts go uncontacted too long",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enabled", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = relationshipAutopilotEnabled,
                            onCheckedChange = { viewModel.setRelationshipAutopilotEnabled(it) },
                        )
                    }

                    Spacer(Modifier.height(12.dp))

                    Text("Morning Briefing", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Auto-briefing when you wake up (calendar, tasks, alerts)",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Enabled", style = MaterialTheme.typography.bodySmall)
                        Switch(checked = morningBriefingEnabled, onCheckedChange = { viewModel.setMorningBriefingEnabled(it) })
                    }
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Speak briefing aloud", style = MaterialTheme.typography.bodySmall)
                        Switch(
                            checked = morningBriefingSpeak,
                            onCheckedChange = { viewModel.setMorningBriefingSpeak(it) },
                            enabled = morningBriefingEnabled,
                        )
                    }
                }
            }
        }

        // ── Missions ──────────────────────────────────────
        item {
            SectionHeader("Learning Missions")
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp)) {
                    val missions by viewModel.missions.collectAsState()
                    val missionLoading by viewModel.missionLoading.collectAsState()

                    if (missions.isEmpty() && !missionLoading) {
                        Text(
                            "No active missions. Create one to have Jarvis autonomously research a topic.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    for (mission in missions.take(5)) {
                        Row(
                            Modifier.fillMaxWidth().padding(vertical = 4.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(mission.topic, style = MaterialTheme.typography.bodyMedium)
                                Text(
                                    "${mission.status} | ${mission.verifiedFindings} findings | ${mission.origin}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }

                    Spacer(Modifier.height(8.dp))

                    var showCreateMission by remember { mutableStateOf(false) }
                    var missionTopic by remember { mutableStateOf("") }

                    if (showCreateMission) {
                        OutlinedTextField(
                            value = missionTopic,
                            onValueChange = { missionTopic = it },
                            label = { Text("Topic to research") },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                        )
                        Spacer(Modifier.height(4.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilledTonalButton(
                                onClick = {
                                    if (missionTopic.isNotBlank()) {
                                        viewModel.createMission(missionTopic.trim())
                                        missionTopic = ""
                                        showCreateMission = false
                                    }
                                },
                            ) {
                                Text("Create")
                            }
                            OutlinedButton(onClick = {
                                showCreateMission = false
                                missionTopic = ""
                            }) {
                                Text("Cancel")
                            }
                        }
                    } else {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilledTonalButton(onClick = { showCreateMission = true }) {
                                Text("New Mission")
                            }
                            OutlinedButton(onClick = { viewModel.loadMissions() }) {
                                Text("Refresh")
                            }
                        }
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
