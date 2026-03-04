package com.jarvis.assistant.feature.health

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tracks screen on/off events via BroadcastReceiver for sleep estimation.
 *
 * Must be [register]ed from a foreground service ([JarvisService]) and
 * [unregister]ed in its onDestroy. Keeps an in-memory ring buffer of the
 * last [maxEvents] events.
 */
@Singleton
class ScreenStateTracker @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    data class ScreenEvent(val isOn: Boolean, val timestamp: Long)

    private val events = mutableListOf<ScreenEvent>()
    private val maxEvents = 500
    @Volatile private var registered = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            val isOn = intent.action == Intent.ACTION_SCREEN_ON
            synchronized(events) {
                events.add(ScreenEvent(isOn, System.currentTimeMillis()))
                if (events.size > maxEvents) events.removeAt(0)
            }
        }
    }

    fun register() {
        if (registered) return
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
        }
        context.registerReceiver(receiver, filter)
        registered = true
    }

    fun unregister() {
        if (!registered) return
        try { context.unregisterReceiver(receiver) } catch (_: Exception) {}
        registered = false
    }

    fun getEventsSince(sinceMs: Long): List<ScreenEvent> = synchronized(events) {
        events.filter { it.timestamp >= sinceMs }.toList()
    }
}
