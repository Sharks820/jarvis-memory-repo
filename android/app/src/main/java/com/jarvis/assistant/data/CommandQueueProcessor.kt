package com.jarvis.assistant.data

import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Queues commands locally and flushes them to the desktop engine.
 *
 * If the desktop is unreachable the command stays in Room with status "pending"
 * and is retried on the next sync cycle.
 */
@Singleton
class CommandQueueProcessor @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val commandQueueDao: CommandQueueDao,
    private val conversationDao: ConversationDao,
) {
    /**
     * Queue a user command.  Inserts into the local DB then immediately
     * attempts to send it to the desktop.
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
        }

        return id
    }

    /** Flush all pending commands to the desktop. */
    suspend fun flushPending() {
        val pending = commandQueueDao.getPending()
        for (cmd in pending) {
            if (cmd.retryCount >= MAX_RETRIES) {
                commandQueueDao.updateStatus(cmd.id, "failed")
                continue
            }
            try {
                sendCommand(cmd.id)
            } catch (e: Exception) {
                commandQueueDao.incrementRetry(cmd.id)
                Log.w(TAG, "Retry failed for command ${cmd.id}: ${e.message}")
            }
        }
    }

    private suspend fun sendCommand(id: Long) {
        val cmd = commandQueueDao.getById(id) ?: return
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
    }

    companion object {
        private const val TAG = "CmdQueue"
        private const val MAX_RETRIES = 5
    }
}
