package com.jarvis.assistant.api

import android.content.Context
import com.jarvis.assistant.security.CryptoHelper
import dagger.hilt.android.qualifiers.ApplicationContext
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
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
) {
    private val crypto by lazy { CryptoHelper(context) }

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
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            })
            .build()
    }

    /** Lazily build Retrofit using the current base URL from encrypted prefs. */
    fun api(): JarvisApi {
        val baseUrl = crypto.getBaseUrl().ifBlank { "http://127.0.0.1:8787" }
        return Retrofit.Builder()
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(okHttp)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(JarvisApi::class.java)
    }
}
