package com.jarvis.assistant.api

import com.jarvis.assistant.api.models.BootstrapResponse
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.api.models.CommandResponse
import com.jarvis.assistant.api.models.DashboardResponse
import com.jarvis.assistant.api.models.HealthResponse
import com.jarvis.assistant.api.models.SettingsResponse
import com.jarvis.assistant.api.models.SpamCandidatesResponse
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST

/** Retrofit interface matching the desktop engine's mobile API (port 8787). */
interface JarvisApi {

    @GET("/health")
    suspend fun health(): HealthResponse

    @POST("/bootstrap")
    suspend fun bootstrap(
        @Header("X-Jarvis-Master-Password") masterPassword: String,
        @Body body: Map<String, String>,
    ): BootstrapResponse

    @POST("/command")
    suspend fun sendCommand(@Body request: CommandRequest): CommandResponse

    @GET("/settings")
    suspend fun getSettings(): SettingsResponse

    @POST("/settings")
    suspend fun setSettings(@Body settings: Map<String, Any>): Map<String, Any>

    @GET("/dashboard")
    suspend fun getDashboard(): DashboardResponse

    /**
     * Fetch spam candidates directly (future desktop endpoint).
     * Currently not implemented on desktop; SpamDatabaseSync falls back
     * to the /command endpoint with "show spam report".
     */
    @GET("/spam/candidates")
    suspend fun getSpamCandidates(): SpamCandidatesResponse
}
