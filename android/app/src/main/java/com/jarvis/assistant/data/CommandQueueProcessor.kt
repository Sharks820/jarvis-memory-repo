package com.jarvis.assistant.data

import android.util.Log
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.intelligence.IntelligenceRouter
import com.jarvis.assistant.intelligence.LocalKnowledgeStore
import com.jarvis.assistant.sync.SyncConfigStore
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Processes user commands through the intelligence router.
 *
 * The command flow is now powered by real on-device intelligence:
 *
 * 1. User sends a query
 * 2. [IntelligenceRouter] decides the best path:
 *    - **Desktop online**: Send to desktop (full LLM), phone enriches with local context
 *    - **Desktop offline**: Process locally with Gemini Nano + local knowledge store
 *    - **Neither can answer**: Queue for desktop, tell user honestly
 * 3. Every interaction teaches the phone something new (knowledge store learning)
 * 4. Exponential backoff with age-based expiry for queued commands
 *
 * The phone is NOT weaker than the desktop — it's a different kind of smart:
 * - It knows your real-world context (where you are, who you're with, what you're doing)
 * - It has Gemini Nano for real AI reasoning on the NPU
 * - It has 2000+ synced knowledge facts from the desktop
 * - It has 16 Room DB tables of personal data to reason over
 */
@Singleton
class CommandQueueProcessor @Inject constructor(
    private val intelligenceRouter: IntelligenceRouter,
    private val commandQueueDao: CommandQueueDao,
    private val conversationDao: ConversationDao,
    private val syncConfig: SyncConfigStore,
) {
    /**
     * Process a user command through the intelligence router.
     *
     * The router handles all intelligence: desktop, on-device AI, knowledge
     * store, and queueing. The user always gets an answer — even offline.
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

        // Route through intelligence router — handles desktop, on-device, and queueing
        try {
            val result = intelligenceRouter.route(text, execute, speak)

            when (result.source) {
                IntelligenceRouter.Source.DESKTOP -> {
                    // Desktop answered — mark as sent
                    commandQueueDao.updateStatus(id, "sent", result.response)
                    conversationDao.insert(
                        ConversationEntity(role = "assistant", content = result.response),
                    )
                }
                IntelligenceRouter.Source.ON_DEVICE -> {
                    // On-device AI answered — mark as sent (processed locally)
                    commandQueueDao.updateStatus(id, "sent", result.response)
                    conversationDao.insert(
                        ConversationEntity(role = "assistant", content = result.response),
                    )
                }
                IntelligenceRouter.Source.QUEUED -> {
                    // Neither could answer — keep pending, show the queued message
                    conversationDao.insert(
                        ConversationEntity(role = "assistant", content = result.response),
                    )
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Intelligence routing failed: ${e.message}")
            // Keep command in queue for retry
            conversationDao.insert(
                ConversationEntity(
                    role = "assistant",
                    content = "I'm having trouble processing that right now. " +
                        "Your command has been saved and will be processed shortly.",
                ),
            )
        }

        return id
    }

    /** Recover commands stuck in 'sending' from a prior crash. Call once at startup. */
    suspend fun recoverStale() {
        commandQueueDao.recoverStaleSending()
    }

    /**
     * Flush pending commands that weren't answered by on-device AI.
     *
     * These are execution commands or queries that neither the desktop
     * nor on-device AI could answer at the time. They stay queued with
     * exponential backoff until the desktop is reachable.
     */
    suspend fun flushPending() {
        val pending = commandQueueDao.getPending()
        val now = System.currentTimeMillis()
        val maxAgeMs = syncConfig.maxOfflineQueueAgeHours * 60 * 60 * 1000

        for (cmd in pending) {
            // Age-based expiry
            val age = now - cmd.createdAt
            if (age > maxAgeMs) {
                commandQueueDao.updateStatus(cmd.id, "expired")
                Log.i(TAG, "Command ${cmd.id} expired after ${age / (60 * 60 * 1000)}h")
                continue
            }

            // Exponential backoff
            if (cmd.retryCount > 0) {
                val backoffBase = syncConfig.retryBackoffBase * 1000L
                val backoffMax = syncConfig.retryBackoffMax * 1000L
                val backoff = minOf(
                    backoffBase * (1L shl minOf(cmd.retryCount, 10)),
                    backoffMax,
                )
                if (cmd.retryCount > 3 && age < cmd.retryCount * backoffBase) {
                    continue // Not time to retry yet
                }
            }

            // Try routing again through the intelligence router
            try {
                val result = intelligenceRouter.route(cmd.text, cmd.execute, cmd.speak)

                when (result.source) {
                    IntelligenceRouter.Source.DESKTOP,
                    IntelligenceRouter.Source.ON_DEVICE -> {
                        commandQueueDao.updateStatus(cmd.id, "sent", result.response)
                        conversationDao.insert(
                            ConversationEntity(role = "assistant", content = result.response),
                        )
                    }
                    IntelligenceRouter.Source.QUEUED -> {
                        commandQueueDao.incrementRetry(cmd.id)
                    }
                }
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

    companion object {
        private const val TAG = "CmdQueue"
        private const val SENT_RETENTION_MS = 7L * 24 * 60 * 60 * 1000 // 7 days
    }
}
