package com.jarvis.assistant.intelligence

import android.content.Context
import android.util.Log
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.DocumentDao
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * On-device intelligence engine for the S25 Ultra.
 *
 * This is NOT a cache. This is a brain. The phone has its own AI that can:
 * 1. **Reason** using Google's Gemini Nano (on-device, via AICore/ML Kit)
 * 2. **Remember** from all 16 Room entities — contacts, habits, locations,
 *    medications, transactions, documents, conversations, context state
 * 3. **Synthesize** context from multiple data sources to answer questions
 * 4. **Learn** from every interaction, building intelligence locally
 * 5. **Operate independently** — full answers without desktop
 *
 * When the desktop is available, both brains combine: the phone enriches
 * its local intelligence from the desktop's knowledge graph, and the desktop
 * absorbs the phone's real-world observations (location, interactions, context).
 *
 * Architecture:
 * - Gemini Nano handles natural language understanding and generation
 * - [LocalKnowledgeStore] provides the factual knowledge base
 * - [ContextAssembler] builds rich context from all phone sensors/data
 * - [IntelligenceRouter] decides: on-device vs desktop vs combined
 */
@Singleton
class OnDeviceIntelligence @Inject constructor(
    @ApplicationContext private val context: Context,
    private val knowledgeStore: LocalKnowledgeStore,
    private val contextAssembler: ContextAssembler,
    private val conversationDao: ConversationDao,
) {
    @Volatile private var geminiAvailable: Boolean? = null

    /**
     * Process a query entirely on-device.
     *
     * Builds rich context from all local data, feeds it to Gemini Nano
     * (or the knowledge engine fallback), and returns a full response.
     *
     * This is NOT a cached lookup. This is actual AI reasoning over local data.
     *
     * @param query The user's natural language query
     * @return An intelligent response, or null if on-device processing isn't possible
     */
    suspend fun processLocally(query: String): String? {
        val queryLower = query.lowercase().trim()

        // Build context from all available local data
        val localContext = contextAssembler.assembleContext(queryLower)

        // Try Gemini Nano first (real on-device AI)
        val geminiResponse = tryGeminiNano(query, localContext)
        if (geminiResponse != null) return geminiResponse

        // Fallback: knowledge-based reasoning using local data
        return knowledgeStore.reason(queryLower, localContext)
    }

    /**
     * Attempt to use Gemini Nano via Google AI Edge SDK.
     *
     * The S25 Ultra has Gemini Nano built into the NPU via AICore.
     * This provides real generative AI on-device — not pattern matching,
     * not retrieval, actual language model reasoning.
     *
     * If Gemini Nano is not available (older device, not downloaded),
     * returns null and we fall back to knowledge-based reasoning.
     */
    private suspend fun tryGeminiNano(query: String, localContext: String): String? {
        try {
            // Check availability (cache the result)
            if (geminiAvailable == false) return null

            val cls = Class.forName("com.google.ai.edge.aicore.GenerativeModel")
            if (geminiAvailable == null) {
                geminiAvailable = true
                Log.i(TAG, "Gemini Nano available on device")
            }

            // Build the prompt with full local context
            val prompt = buildGeminiPrompt(query, localContext)

            // Use reflection to invoke Gemini Nano without compile-time dependency
            // This allows the app to work on devices without AICore
            val configCls = Class.forName("com.google.ai.edge.aicore.GenerationConfig")
            val configBuilder = configCls.getDeclaredMethod("builder").invoke(null)
            val builderCls = configBuilder.javaClass
            builderCls.getDeclaredMethod("setMaxOutputTokens", Int::class.java)
                .invoke(configBuilder, 1024)
            builderCls.getDeclaredMethod("setTemperature", Float::class.java)
                .invoke(configBuilder, 0.7f)
            val config = builderCls.getDeclaredMethod("build").invoke(configBuilder)

            val model = cls.getDeclaredConstructor(configCls).newInstance(config)
            val generateMethod = cls.getDeclaredMethod(
                "generateContent", String::class.java,
            )
            val result = generateMethod.invoke(model, prompt)
            val text = result?.javaClass?.getDeclaredMethod("getText")?.invoke(result) as? String

            return text?.takeIf { it.isNotBlank() }
        } catch (e: ClassNotFoundException) {
            // AICore SDK not available on this device
            if (geminiAvailable == null) {
                geminiAvailable = false
                Log.i(TAG, "Gemini Nano not available, using knowledge engine")
            }
            return null
        } catch (e: Exception) {
            Log.w(TAG, "Gemini Nano inference failed: ${e.message}")
            return null
        }
    }

    /**
     * Build a prompt for Gemini Nano that includes full local context.
     *
     * This is what makes the phone's AI as smart as possible — it knows
     * everything the phone knows about you, your schedule, habits, contacts,
     * medications, spending, documents, and current context.
     */
    private fun buildGeminiPrompt(query: String, localContext: String): String {
        return """You are Jarvis, a personal AI assistant for Conner. You are running on his Samsung Galaxy S25 Ultra.
You have access to Conner's local data and should use it to give personalized, accurate answers.
Be direct, concise, and helpful. If you don't have enough information, say so honestly.

=== CONNER'S CURRENT CONTEXT ===
$localContext

=== CONNER'S QUESTION ===
$query

=== YOUR RESPONSE ===
"""
    }

    companion object {
        private const val TAG = "OnDeviceAI"
    }
}
