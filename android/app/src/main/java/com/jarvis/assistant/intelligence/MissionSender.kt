package com.jarvis.assistant.intelligence

import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.MissionCreateRequest
import com.jarvis.assistant.api.MissionCreateResponse
import com.jarvis.assistant.api.MissionDto
import com.jarvis.assistant.api.MissionStatusResponse
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Sends learning missions from the phone to the desktop brain.
 *
 * The phone has unique context that the desktop lacks: real-world
 * encounters, overheard topics, things you see on the go, conversations
 * with people, locations visited. This utility lets the phone create
 * learning missions directly — no voice command parsing required.
 *
 * Example triggers:
 * - After a call about a topic you want to learn more about
 * - When visiting a new place and wanting background research
 * - When a notification mentions something unfamiliar
 * - Manual request via the UI ("Research this for me")
 */
@Singleton
class MissionSender @Inject constructor(
    private val apiClient: JarvisApiClient,
) {
    /**
     * Create a learning mission on the desktop.
     *
     * @param topic What to research (max 200 chars)
     * @param objective Specific learning goal (optional, max 400 chars)
     * @param sources Research sources — defaults to desktop's standard set
     * @return mission ID if created, null on failure
     */
    suspend fun createMission(
        topic: String,
        objective: String = "",
        sources: List<String> = emptyList(),
    ): String? {
        val trimmedTopic = topic.trim().take(200)
        if (trimmedTopic.isBlank()) {
            Log.w(TAG, "Empty topic — skipping mission creation")
            return null
        }
        return try {
            val response: MissionCreateResponse = apiClient.api().createMission(
                MissionCreateRequest(
                    topic = trimmedTopic,
                    objective = objective.trim().take(400),
                    sources = sources,
                ),
            )
            if (response.ok) {
                Log.i(TAG, "Mission created: ${response.missionId} — ${response.topic}")
                response.missionId
            } else {
                Log.w(TAG, "Mission creation returned ok=false")
                null
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to create mission: ${e.message}")
            null
        }
    }

    /**
     * Get current mission status from the desktop.
     *
     * @return list of missions, or empty list on failure
     */
    suspend fun getMissionStatus(): List<MissionDto> {
        return try {
            val response: MissionStatusResponse = apiClient.api().getMissionStatus()
            if (response.ok) response.missions else emptyList()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to get mission status: ${e.message}")
            emptyList()
        }
    }

    /**
     * Create a mission triggered by a phone call conversation.
     *
     * After a call where an unfamiliar topic came up, the phone
     * can auto-suggest a learning mission to research it.
     */
    suspend fun createFromCallContext(
        topic: String,
        contactName: String?,
    ): String? {
        val objective = if (contactName != null) {
            "Research topic discussed with $contactName"
        } else {
            "Research topic from recent phone conversation"
        }
        return createMission(topic, objective)
    }

    /**
     * Create a mission triggered by a notification topic.
     *
     * When a notification mentions something the knowledge graph
     * doesn't cover, queue a mission to learn about it.
     */
    suspend fun createFromNotification(
        topic: String,
        appName: String,
    ): String? {
        return createMission(
            topic = topic,
            objective = "Research topic surfaced by $appName notification",
        )
    }

    companion object {
        private const val TAG = "MissionSender"
    }
}
