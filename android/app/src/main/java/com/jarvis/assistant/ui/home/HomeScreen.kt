package com.jarvis.assistant.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Circle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import java.util.Calendar

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(viewModel: HomeViewModel = hiltViewModel()) {
    val uiState by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Jarvis") },
                actions = {
                    Icon(
                        Icons.Filled.Circle,
                        contentDescription = "connection",
                        tint = if (uiState is HomeViewModel.UiState.Success) Color(0xFF4CAF50) else Color(0xFFF44336),
                        modifier = Modifier.size(12.dp),
                    )
                    IconButton(onClick = { viewModel.loadDashboard() }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
    ) { padding ->
        when (val state = uiState) {
            is HomeViewModel.UiState.Loading -> {
                Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            }
            is HomeViewModel.UiState.Error -> {
                Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(state.message, style = MaterialTheme.typography.bodyLarge)
                        Spacer(Modifier.height(16.dp))
                        FilledTonalButton(onClick = { viewModel.loadDashboard() }) {
                            Text("Retry")
                        }
                    }
                }
            }
            is HomeViewModel.UiState.Success -> {
                val data = state.data
                PullToRefreshBox(
                    isRefreshing = false,
                    onRefresh = { viewModel.loadDashboard() },
                    modifier = Modifier.padding(padding),
                ) {
                    LazyColumn(
                        contentPadding = PaddingValues(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        // Greeting
                        item {
                            Card(
                                modifier = Modifier.fillMaxWidth(),
                                colors = CardDefaults.cardColors(
                                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                                ),
                            ) {
                                Text(
                                    text = greeting(),
                                    style = MaterialTheme.typography.headlineMedium,
                                    modifier = Modifier.padding(16.dp),
                                )
                            }
                        }

                        // Jarvis score
                        data.jarvis?.let { score ->
                            item {
                                Card(Modifier.fillMaxWidth()) {
                                    Column(Modifier.padding(16.dp)) {
                                        Text("Intelligence Score", style = MaterialTheme.typography.titleMedium)
                                        Spacer(Modifier.height(4.dp))
                                        Text(
                                            "${score.scorePct}%",
                                            style = MaterialTheme.typography.displayLarge,
                                            color = MaterialTheme.colorScheme.primary,
                                        )
                                        if (score.latestModel.isNotBlank()) {
                                            Text(
                                                "Model: ${score.latestModel}",
                                                style = MaterialTheme.typography.labelSmall,
                                            )
                                        }
                                    }
                                }
                            }
                        }

                        // Rankings
                        if (data.ranking.isNotEmpty()) {
                            item {
                                Text("Rankings", style = MaterialTheme.typography.titleMedium)
                            }
                            items(data.ranking) { entry ->
                                Card(Modifier.fillMaxWidth()) {
                                    Row(
                                        Modifier.padding(12.dp).fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                    ) {
                                        Text(entry.name, style = MaterialTheme.typography.bodyLarge)
                                        Text("${entry.scorePct}%", style = MaterialTheme.typography.bodyLarge)
                                    }
                                }
                            }
                        }

                        // ETAs
                        if (data.etas.isNotEmpty()) {
                            item {
                                Text("Learning ETAs", style = MaterialTheme.typography.titleMedium)
                            }
                            items(data.etas) { eta ->
                                Card(Modifier.fillMaxWidth()) {
                                    Column(Modifier.padding(12.dp)) {
                                        Text(eta.targetName, style = MaterialTheme.typography.bodyLarge)
                                        val detail = eta.eta?.let { info ->
                                            when {
                                                info.days != null -> "${info.status} — ~${info.days} days"
                                                info.runs != null -> "${info.status} — ~${info.runs} runs"
                                                else -> info.status
                                            }
                                        } ?: "Unknown"
                                        Text(detail, style = MaterialTheme.typography.bodyMedium)
                                    }
                                }
                            }
                        }

                        // Quick actions
                        item {
                            Spacer(Modifier.height(8.dp))
                            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                val actions = listOf("Daily Briefing", "Search Memory", "Sync Now")
                                items(actions) { label ->
                                    FilledTonalButton(onClick = { /* handled by nav */ }) {
                                        Text(label)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun greeting(): String {
    val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
    return when {
        hour < 12 -> "Good morning, sir"
        hour < 17 -> "Good afternoon, sir"
        else -> "Good evening, sir"
    }
}
