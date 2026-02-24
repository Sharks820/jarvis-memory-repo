package com.jarvis.assistant.ui.theme

import android.os.Build
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

private val JarvisDarkColorScheme = darkColorScheme(
    primary = JarvisPrimary,
    secondary = JarvisSecondary,
    tertiary = JarvisTertiary,
    background = JarvisBackground,
    surface = JarvisSurface,
    surfaceVariant = JarvisSurfaceVariant,
    onPrimary = JarvisOnPrimary,
    onSecondary = JarvisOnSecondary,
    onBackground = JarvisOnBackground,
    onSurface = JarvisOnSurface,
    error = JarvisError,
    onError = JarvisOnError,
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    // Always dark. Use Material You dynamic colors on Android 12+ (API 31+).
    val colorScheme = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        dynamicDarkColorScheme(LocalContext.current)
    } else {
        JarvisDarkColorScheme
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = JarvisTypography,
        content = content,
    )
}
