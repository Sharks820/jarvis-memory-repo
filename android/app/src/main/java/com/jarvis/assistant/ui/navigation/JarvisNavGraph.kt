package com.jarvis.assistant.ui.navigation

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.jarvis.assistant.feature.voice.VoiceEngine
import com.jarvis.assistant.ui.chat.ChatScreen
import com.jarvis.assistant.ui.home.HomeScreen
import com.jarvis.assistant.ui.memory.MemoryScreen
import com.jarvis.assistant.ui.onboarding.BootstrapScreen
import com.jarvis.assistant.ui.settings.SettingsScreen

@Composable
fun JarvisNavGraph(
    isBootstrapped: Boolean,
    voiceEngine: VoiceEngine,
) {
    val navController = rememberNavController()
    val startDest = if (isBootstrapped) "home" else "bootstrap"

    Scaffold(
        bottomBar = {
            // Only show bottom nav when NOT on bootstrap screen.
            val current = navController.currentBackStackEntry?.destination?.route
            if (current != "bootstrap") {
                BottomNavBar(navController)
            }
        },
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = startDest,
            modifier = Modifier.padding(innerPadding),
        ) {
            composable("bootstrap") {
                BootstrapScreen(
                    onBootstrapComplete = {
                        navController.navigate("home") {
                            popUpTo("bootstrap") { inclusive = true }
                        }
                    },
                )
            }
            composable("home") {
                HomeScreen()
            }
            composable("chat") {
                ChatScreen(voiceEngine = voiceEngine)
            }
            composable("memory") {
                MemoryScreen()
            }
            composable("settings") {
                SettingsScreen(
                    onResetBootstrap = {
                        navController.navigate("bootstrap") {
                            popUpTo(0) { inclusive = true }
                        }
                    },
                )
            }
        }
    }
}
