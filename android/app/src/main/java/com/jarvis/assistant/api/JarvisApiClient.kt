package com.jarvis.assistant.api

import android.content.Context
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
 * Base URL and credentials are read from [CryptoHelper] (EncryptedSharedPreferences).
 * All requests are HMAC-signed via [HmacInterceptor].
 */
@Singleton
class JarvisApiClient @Inject constructor(
    @ApplicationContext private val context: Context,
    private val crypto: CryptoHelper,
) {

    private val okHttp by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .addInterceptor(HmacInterceptor {
                HmacInterceptor.Credentials(
                    token = crypto.getToken(),
                    signingKey = crypto.getSigningKey(),
                    deviceId = crypto.getDeviceId(),
                )
            })
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
}
