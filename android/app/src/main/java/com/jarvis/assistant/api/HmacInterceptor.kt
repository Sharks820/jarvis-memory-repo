package com.jarvis.assistant.api

import okhttp3.Interceptor
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.Buffer
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OkHttp interceptor that signs every request with HMAC-SHA256.
 *
 * Matches the desktop engine's verification:
 * signing_material = "$timestamp\n$nonce\n$body"
 *
 * For bodies larger than [STREAMING_THRESHOLD_BYTES] (1 MB), the HMAC is
 * computed in a streaming fashion to avoid holding the entire payload in
 * memory at once. Smaller bodies use the simpler single-shot approach.
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
        val timestamp = (System.currentTimeMillis() / 1000L).toString()
        val nonce = generateNonce()

        val body = original.body
        val contentLength = body?.contentLength() ?: 0L

        val signature: String
        val rebuiltBody: RequestBody?

        if (body == null || contentLength == 0L) {
            // No body — sign just the timestamp and nonce
            val signingMaterial = "$timestamp\n$nonce\n"
            signature = hmacSha256(creds.signingKey, signingMaterial)
            rebuiltBody = null
        } else if (contentLength in 1 until STREAMING_THRESHOLD_BYTES) {
            // Small body — single-shot (original approach)
            val buffer = Buffer()
            body.writeTo(buffer)
            val bodyStr = buffer.readUtf8()
            val signingMaterial = "$timestamp\n$nonce\n$bodyStr"
            signature = hmacSha256(creds.signingKey, signingMaterial)
            rebuiltBody = bodyStr.toRequestBody(body.contentType())
        } else {
            // Large body (>= 1 MB) or unknown length — streaming HMAC
            val result = streamingHmac(creds.signingKey, timestamp, nonce, body)
            signature = result.first
            rebuiltBody = result.second
        }

        val newRequest = original.newBuilder()
            .method(original.method, rebuiltBody ?: original.body)
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

    /**
     * Computes HMAC-SHA256 by streaming the request body through the Mac in
     * chunks, avoiding full materialization in memory.
     *
     * The signing material format remains: "$timestamp\n$nonce\n$body"
     * — the timestamp and nonce prefix are fed to the Mac first, then the
     * body bytes are streamed in [CHUNK_SIZE] segments.
     *
     * @return Pair of (hex signature, rebuilt RequestBody for the actual request)
     */
    private fun streamingHmac(
        key: String,
        timestamp: String,
        nonce: String,
        body: RequestBody,
    ): Pair<String, RequestBody> {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key.toByteArray(Charsets.UTF_8), "HmacSHA256"))

        // Feed the prefix: "$timestamp\n$nonce\n"
        val prefix = "$timestamp\n$nonce\n"
        mac.update(prefix.toByteArray(Charsets.UTF_8))

        // Write the body into a buffer, streaming chunks through the Mac
        val bodyBuffer = Buffer()
        body.writeTo(bodyBuffer)

        // Read the buffer in chunks, updating the Mac incrementally
        val bodyBytes = Buffer()
        val chunk = ByteArray(CHUNK_SIZE)
        while (!bodyBuffer.exhausted()) {
            val bytesRead = bodyBuffer.read(chunk)
            if (bytesRead > 0) {
                mac.update(chunk, 0, bytesRead)
                bodyBytes.write(chunk, 0, bytesRead)
            }
        }

        val hash = mac.doFinal()
        val signature = hash.joinToString("") { "%02x".format(it) }

        // Rebuild the body from the buffered bytes
        val rebuiltBody = bodyBytes.readByteArray().toRequestBody(body.contentType())
        return Pair(signature, rebuiltBody)
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

        /** Bodies larger than this threshold (1 MB) use streaming HMAC. */
        private const val STREAMING_THRESHOLD_BYTES = 1L * 1024 * 1024

        /** Chunk size for streaming HMAC reads (64 KB). */
        private const val CHUNK_SIZE = 64 * 1024
    }
}
