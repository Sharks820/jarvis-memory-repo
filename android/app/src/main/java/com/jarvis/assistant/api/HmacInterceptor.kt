package com.jarvis.assistant.api

import okhttp3.Interceptor
import okhttp3.Response
import okhttp3.RequestBody.Companion.toRequestBody
import okio.Buffer
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OkHttp interceptor that signs every request with HMAC-SHA256.
 *
 * Matches the desktop engine's verification:
 * signing_material = "$timestamp\n$nonce\n$body"
 */
class HmacInterceptor(
    private val credentialsProvider: () -> Credentials,
) : Interceptor {

    data class Credentials(
        val token: String,
        val signingKey: String,
        val deviceId: String,
    )

    override fun intercept(chain: Interceptor.Chain): Response {
        val creds = credentialsProvider()
        if (creds.token.isBlank() || creds.signingKey.isBlank()) {
            return chain.proceed(chain.request())
        }

        val original = chain.request()
        val bodyStr = original.body?.let { body ->
            val buffer = Buffer()
            body.writeTo(buffer)
            buffer.readUtf8()
        } ?: ""

        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val nonce = generateNonce()
        val signingMaterial = "$timestamp\n$nonce\n$bodyStr"
        val signature = hmacSha256(creds.signingKey, signingMaterial)

        // Rebuild request with a fresh body (the original was consumed by writeTo)
        val newRequest = original.newBuilder()
            .method(original.method, bodyStr.toRequestBody(original.body?.contentType()))
            .header("Authorization", "Bearer ${creds.token}")
            .header("X-Jarvis-Timestamp", timestamp)
            .header("X-Jarvis-Nonce", nonce)
            .header("X-Jarvis-Signature", signature)
            .apply {
                if (creds.deviceId.isNotBlank()) {
                    header("X-Jarvis-Device-Id", creds.deviceId)
                }
            }
            .build()

        return chain.proceed(newRequest)
    }

    private fun hmacSha256(key: String, message: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        val hash = mac.doFinal(message.toByteArray(Charsets.UTF_8))
        return hash.joinToString("") { "%02x".format(it) }
    }

    private fun generateNonce(): String {
        val bytes = ByteArray(16)
        SECURE_RANDOM.nextBytes(bytes)
        return bytes.joinToString("") { "%02x".format(it) }
    }

    companion object {
        private val SECURE_RANDOM = SecureRandom()
    }
}
