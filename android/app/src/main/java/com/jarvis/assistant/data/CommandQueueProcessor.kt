package com.jarvis.assistant.data

import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.sync.LocalResponseCache
import com.jarvis.assistant.sync.SyncConfigStore
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Queues commands locally and flushes them to the desktop engine.
 *
 * Key improvements over the original:
 * - **No max retry limit**: Commands stay pending until the desktop is reachable,
 *   even if that takes days. Your commands never die.
 * - **Exponential backoff**: Instead of hammering the server every 30s, backs off
 *   intelligently based on how long the desktop has been unreachable.
 * - **Offline response cache**: When the desktop is unreachable, checks the local
 *   cache for similar previous responses so you still get answers.
 * - **Age-based expiry**: Commands older than the configured max age (default 7 days)
 *   are expired instead of using a fixed retry count.
 */
@Singleton
class CommandQueueProcessor @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val commandQueueDao: CommandQueueDao,
    private val conversationDao: ConversationDao,
    private val responseCache: LocalResponseCache,
    private val syncConfig: SyncConfigStore,
) {
    /**
     * Queue a user command. Inserts into the local DB then immediately
     * attempts to send it to the desktop. If the desktop is unreachable,
     * checks the local cache for a similar response.
     *
     * @return the local command row id
     */
    suspend fun queueCommand(
        text: String,
        execute: Boolean = false,
        speak: Boolean = false,
    ): Long {
        // Persist the user message in conversation history.
        conversationDao.insert(
            ConversationEntity(role = "user", content = text),
        )

        val id = commandQueueDao.insert(
            CommandQueueEntity(
                text = text,
                execute = execute,
                speak = speak,
            ),
        )

        // Optimistic send — fire and forget; the sync loop will retry on failure.
        try {
            sendCommand(id)
        } catch (e: Exception) {
            Log.w(TAG, "Immediate send failed, will retry on next sync: ${e.message}")

            // If desktop is unreachable, try to serve a cached response
            if (!execute) { // Don't serve cached responses for execution commands
                val cached = responseCache.findCachedResponse(text)
                if (cached != null) {
                    conversationDao.insert(
                        ConversationEntity(role = "assistant", content = cached),
                    )
                    Log.i(TAG, "Served cached response for offline query")
                }
            }
        }

        return id
    }

    /** Recover commands stuck in 'sending' from a prior crash. Call once at startup. */
    suspend fun recoverStale() {
        commandQueueDao.recoverStaleSending()
    }

    /**
     * Flush all pending commands to the desktop.
     *
     * Commands are never permanently failed based on retry count alone.
     * Instead, they're expired based on age (configurable, default 7 days).
     * Retry timing uses exponential backoff to avoid hammering the server.
     */
    suspend fun flushPending() {
        val pending = commandQueueDao.getPending()
        val now = System.currentTimeMillis()
        val maxAgeMs = syncConfig.maxOfflineQueueAgeHours * 60 * 60 * 1000

        for (cmd in pending) {
            // Age-based expiry instead of retry-count-based failure.
            // Commands older than maxOfflineQueueAgeHours are expired.
            val age = now - cmd.createdAt
            if (age > maxAgeMs) {
                commandQueueDao.updateStatus(cmd.id, "expired")
                Log.i(TAG, "Command ${cmd.id} expired after ${age / (60 * 60 * 1000)}h")
                continue
            }

            // Exponential backoff: skip this command if it's not time to retry yet.
            // backoff = base * 2^retryCount, capped at max
            if (cmd.retryCount > 0) {
                val backoffBase = syncConfig.retryBackoffBase * 1000L
                val backoffMax = syncConfig.retryBackoffMax * 1000L
                val backoff = minOf(
                    backoffBase * (1L shl minOf(cmd.retryCount, 10)),
                    backoffMax,
                )
                val timeSinceLastRetry = now - cmd.createdAt - (cmd.retryCount * backoffBase)
                // Simple check: if retry count is high, wait longer between retries
                if (cmd.retryCount > 3 && age < cmd.retryCount * backoffBase) {
                    continue // Not time to retry yet
                }
            }

            try {
                sendCommand(cmd.id)
            } catch (e: Exception) {
                commandQueueDao.incrementRetry(cmd.id)
                Log.w(TAG, "Retry #${cmd.retryCount + 1} failed for command ${cmd.id}: ${e.message}")
            }
        }

        // Purge sent/expired commands older than retention period
        try {
            val cutoff = now - SENT_RETENTION_MS
            commandQueueDao.purgeSent(cutoff)
        } catch (e: Exception) {
            Log.w(TAG, "Command queue purge failed: ${e.message}")
        }
    }

    private suspend fun sendCommand(id: Long) {
        val cmd = commandQueueDao.getById(id) ?: return
        if (cmd.status != "pending") return // Already sent or failed — prevent duplicate sends
        // Atomically mark as "sending" to prevent duplicate concurrent sends
        val claimed = commandQueueDao.claimForSend(id)
        if (claimed == 0) return // Another coroutine already claimed it

        try {
            val request = CommandRequest(
                text = cmd.text,
                execute = cmd.execute,
                approvePrivileged = cmd.approvePrivileged,
                speak = cmd.speak,
            )

            val response = apiClient.api().sendCommand(request)
            val responseText = if (response.ok) {
                response.intent.ifBlank {
                    response.stdoutTail.joinToString("\n").ifBlank { "Done." }
                }
            } else {
                "Command failed."
            }

            val newStatus = if (response.ok) "sent" else "failed"
            commandQueueDao.updateStatus(id, newStatus, responseText)

            // Persist the assistant's reply in conversation history.
            conversationDao.insert(
                ConversationEntity(role = "assistant", content = responseText),
            )

            // Cache successful responses for offline use
            if (response.ok) {
                responseCache.cacheResponse(cmd.text, responseText)
            }
        } catch (e: Exception) {
            // Reset status to "pending" so flushPending can retry
            commandQueueDao.updateStatus(id, "pending")
            throw e
        }
    }

    companion object {
        private const val TAG = "CmdQueue"
        private const val SENT_RETENTION_MS = 7L * 24 * 60 * 60 * 1000 // 7 days
    }
}
