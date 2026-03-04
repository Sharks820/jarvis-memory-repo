package com.jarvis.assistant.api

import android.content.Context
import android.util.Log
import com.jarvis.assistant.security.CryptoHelper
import dagger.hilt.android.qualifiers.ApplicationContext
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import com.jarvis.assistant.BuildConfig
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Provides a signed Retrofit client that talks to the desktop engine.
 *
 * Supports **dual-URL connectivity** for reaching the desktop from anywhere:
 * 1. **LAN URL** (primary): Fast, low-latency, used when on same WiFi network
 * 2. **Relay URL** (fallback): Works from anywhere — Cloudflare Tunnel, Tailscale,
 *    ngrok, or any reverse proxy that exposes the desktop's port 8787.
 *
 * The OkHttp interceptor chain handles automatic failover:
 * - Request goes to LAN URL first (sub-100ms on local network)
 * - If LAN fails (timeout, unreachable), retries the same request on relay URL
 * - HMAC signing applies to both URLs identically (same signing key)
 *
 * This means the S25 Ultra works seamlessly whether at home on WiFi,
 * at work on corporate WiFi, or on the road with cellular data.
 *
 * Base URL and credentials are read from [CryptoHelper] (EncryptedSharedPreferences).
 * All requests are HMAC-signed via [HmacInterceptor].
 */
@Singleton
class JarvisApiClient @Inject constructor(
    @ApplicationContext private val context: Context,
    private val crypto: CryptoHelper,
) {
    @Volatile private var relayUrl: String = ""

    private val okHttp by lazy {
        OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)   // Reduced: fail fast on LAN, let relay try
            .readTimeout(120, TimeUnit.SECONDS)    // LLM queries can take 60-90s (model loading, web research)
            .writeTimeout(15, TimeUnit.SECONDS)
            .addInterceptor(HmacInterceptor {
                HmacInterceptor.Credentials(
                    token = crypto.getToken(),
                    signingKey = crypto.getSigningKey(),
                    deviceId = crypto.getDeviceId(),
                )
            })
            // Relay failover interceptor: if LAN request fails, retry via relay URL
            .addInterceptor { chain ->
                val request = chain.request()
                try {
                    val response = chain.proceed(request)
                    response
                } catch (e: java.io.IOException) {
                    val relay = relayUrl
                    if (relay.isNotBlank() && !request.url.toString().startsWith(relay)) {
                        // LAN failed — retry through relay
                        Log.d(TAG, "LAN request failed, retrying via relay: ${e.message}")
                        val relayBase = relay.trimEnd('/')
                        val path = request.url.encodedPath +
                            (request.url.encodedQuery?.let { "?$it" } ?: "")
                        val newUrl = relayBase + path
                        val newRequest = request.newBuilder().url(newUrl).build()
                        try {
                            chain.proceed(newRequest)
                        } catch (e2: java.io.IOException) {
                            // Both LAN and relay failed — report relay error (more actionable)
                            Log.d(TAG, "Relay also failed: ${e2.message}")
                            throw e2
                        }
                    } else {
                        throw e
                    }
                }
            }
            .apply {
                if (BuildConfig.DEBUG) {
                    addInterceptor(HttpLoggingInterceptor().apply {
                        level = HttpLoggingInterceptor.Level.BASIC
                    })
                }
            }
            .build()
    }

    @Volatile private var lastBaseUrl: String = ""
    @Volatile private var cachedApi: JarvisApi? = null

    private fun buildRetrofit(baseUrl: String): Retrofit =
        Retrofit.Builder()
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(okHttp)
            .addConverterFactory(GsonConverterFactory.create())
            .build()

    /** Returns a [JarvisApi] Retrofit proxy, rebuilding if the base URL has changed. */
    fun api(): JarvisApi {
        val currentUrl = crypto.getBaseUrl().ifBlank { "http://127.0.0.1:8787" }
        val existing = cachedApi
        if (existing != null && currentUrl == lastBaseUrl) return existing
        synchronized(this) {
            // Double-check inside lock
            if (cachedApi != null && currentUrl == lastBaseUrl) return cachedApi!!
            lastBaseUrl = currentUrl
            val api = buildRetrofit(currentUrl).create(JarvisApi::class.java)
            cachedApi = api
            return api
        }
    }

    /**
     * Update the relay URL for fallback connectivity.
     * Called when the phone receives sync config from the desktop.
     * The relay URL is used automatically when the LAN URL is unreachable.
     */
    fun updateRelayUrl(url: String) {
        relayUrl = url
    }

    companion object {
        private const val TAG = "ApiClient"
    }
}
