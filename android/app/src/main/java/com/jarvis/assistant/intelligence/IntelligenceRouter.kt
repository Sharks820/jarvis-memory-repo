package com.jarvis.assistant.intelligence

import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.ConversationEntity
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Routes intelligence queries to the best available brain.
 *
 * The phone is NOT a dumb client. It's one half of a two-brain system:
 *
 * **When desktop is reachable:**
 *   Phone sends query → Desktop processes with full LLM power → Response
 *   Meanwhile, phone enriches the request with local context (what you're
 *   doing, where you are, recent interactions) that the desktop doesn't have.
 *   Both brains contribute to every answer.
 *
 * **When desktop is offline:**
 *   Phone processes locally using:
 *   1. Gemini Nano on the S25 Ultra's NPU (real AI, not lookup)
 *   2. Local knowledge store (2000+ facts synced from desktop + learned locally)
 *   3. All 16 Room DB entities (contacts, habits, meds, finance, etc.)
 *   4. Context assembler (knows what you're doing, where, when)
 *
 * **After reconnection:**
 *   Phone syncs local learnings → Desktop absorbs phone intelligence
 *   Desktop syncs new knowledge → Phone absorbs desktop intelligence
 *   Both brains get smarter from the other's experiences
 *
 * This router makes the decision transparent: the user doesn't need to know
 * or care which brain answered. It just works.
 */
@Singleton
class IntelligenceRouter @Inject constructor(
    private val apiClient: JarvisApiClient,
    private val onDeviceAI: OnDeviceIntelligence,
    private val knowledgeStore: LocalKnowledgeStore,
    private val contextAssembler: ContextAssembler,
    private val conversationDao: ConversationDao,
) {
    /** Track whether the desktop was reachable on the last attempt. */
    @Volatile var desktopReachable: Boolean = true
        private set

    /**
     * Route a user query to the best available intelligence source.
     *
     * @param text The user's query
     * @param execute Whether this is an execution command (not a question)
     * @param speak Whether to speak the response
     * @return The response text and which source provided it
     */
    suspend fun route(
        text: String,
        execute: Boolean = false,
        speak: Boolean = false,
    ): RouteResult {
        // Execution commands MUST go to desktop (they modify state)
        if (execute) {
            return tryDesktop(text, execute, speak)
                ?: RouteResult(
                    response = "Desktop is offline. Execution commands require the desktop. " +
                        "Your command has been queued and will execute when the desktop reconnects.",
                    source = Source.QUEUED,
                    wasOffline = true,
                )
        }

        // For questions/queries: try desktop first, fall back to on-device
        val desktopResult = tryDesktop(text, execute, speak)
        if (desktopResult != null) {
            // Desktop answered — also learn from this interaction locally
            learnFromDesktopResponse(text, desktopResult.response)
            return desktopResult
        }

        // Desktop is offline — use on-device intelligence
        val localResult = onDeviceAI.processLocally(text)
        if (localResult != null) {
            return RouteResult(
                response = localResult,
                source = Source.ON_DEVICE,
                wasOffline = true,
            )
        }

        // On-device AI couldn't answer either
        return RouteResult(
            response = "I'm working offline right now and don't have enough information " +
                "to answer that specific question. Your query has been queued for when " +
                "the desktop reconnects. In the meantime, I can help with your schedule, " +
                "medications, contacts, spending, habits, and anything else I've learned locally.",
            source = Source.QUEUED,
            wasOffline = true,
        )
    }

    private suspend fun tryDesktop(
        text: String,
        execute: Boolean,
        speak: Boolean,
    ): RouteResult? {
        return try {
            val request = CommandRequest(
                text = text,
                execute = execute,
                speak = speak,
            )
            val response = apiClient.api().sendCommand(request)
            desktopReachable = true

            if (response.ok) {
                // Extract the actual LLM response:
                // 1. Use the dedicated "response" field from the API
                // 2. Fall back to parsing "response=" lines from stdout_tail
                // 3. Last resort: join stdout_tail or show "Done."
                val responseText = response.response.ifBlank {
                    response.stdoutTail
                        .lastOrNull { it.startsWith("response=") }
                        ?.substringAfter("response=")
                        ?.ifBlank { null }
                        ?: response.stdoutTail
                            .filter { !it.startsWith("intent=") && !it.startsWith("reason=") && !it.startsWith("status_code=") }
                            .joinToString("\n")
                            .ifBlank { "Done." }
                }
                RouteResult(
                    response = responseText,
                    source = Source.DESKTOP,
                    wasOffline = false,
                )
            } else {
                RouteResult(
                    response = "Command processed but returned an error.",
                    source = Source.DESKTOP,
                    wasOffline = false,
                )
            }
        } catch (e: Exception) {
            desktopReachable = false
            Log.d(TAG, "Desktop unreachable: ${e.message}")
            null
        }
    }

    /**
     * Learn from successful desktop interactions.
     *
     * When the desktop gives a response, the phone extracts knowledge from it
     * and stores it locally. This means every desktop interaction makes the
     * phone smarter — even for future offline use.
     */
    private fun learnFromDesktopResponse(query: String, response: String) {
        try {
            // Extract factual statements from the response for local storage
            val queryLower = query.lowercase()
            val category = when {
                queryLower.containsAny("medication", "health", "pill") -> LocalKnowledgeStore.CAT_HEALTH
                queryLower.containsAny("spend", "money", "finance") -> LocalKnowledgeStore.CAT_FINANCE
                queryLower.containsAny("schedule", "calendar", "event") -> LocalKnowledgeStore.CAT_SCHEDULE
                queryLower.containsAny("who", "contact", "friend") -> LocalKnowledgeStore.CAT_SOCIAL
                queryLower.containsAny("where", "location", "place") -> LocalKnowledgeStore.CAT_LOCATION
                queryLower.containsAny("work", "project", "task") -> LocalKnowledgeStore.CAT_WORK
                else -> LocalKnowledgeStore.CAT_GENERAL
            }

            // Store the Q&A as a knowledge fact for future reference
            if (response.length > 20 && response.length < 1000) {
                val keywords = query.lowercase().split("\\s+".toRegex())
                    .filter { it.length > 3 }
                knowledgeStore.addFact(
                    content = "Q: ${query.take(200)}\nA: ${response.take(500)}",
                    category = category,
                    confidence = 0.7,
                    keywords = keywords,
                    source = "phone",
                )
            }
        } catch (e: Exception) {
            Log.d(TAG, "Learning from desktop response failed: ${e.message}")
        }
    }

    private fun String.containsAny(vararg words: String): Boolean =
        words.any { this.contains(it) }

    companion object {
        private const val TAG = "IntelRouter"
    }

    /** Where the response came from. */
    enum class Source {
        DESKTOP,    // Full desktop LLM processing
        ON_DEVICE,  // Gemini Nano or local knowledge engine
        QUEUED,     // Neither could answer, queued for later
    }

    /** Result of routing a query. */
    data class RouteResult(
        val response: String,
        val source: Source,
        val wasOffline: Boolean,
    )
}
