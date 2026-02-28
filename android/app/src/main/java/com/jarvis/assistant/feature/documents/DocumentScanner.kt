package com.jarvis.assistant.feature.documents

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.Log
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Document scanning pipeline: capture image, run ML Kit OCR, categorize,
 * save to internal storage, and insert into Room.
 */
@Singleton
class DocumentScanner @Inject constructor(
    @ApplicationContext private val context: Context,
    private val documentDao: DocumentDao,
    private val categorizer: DocumentCategorizer,
) {

    private val textRecognizer by lazy {
        TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
    }

    /**
     * Main scanning pipeline: load image, run OCR, categorize, save to
     * internal storage, and insert into Room.
     */
    suspend fun scanAndExtract(imageUri: Uri): ScannedDocumentEntity {
        // 1. Load image for ML Kit
        val inputImage = InputImage.fromFilePath(context, imageUri)

        // 2. Run OCR
        val textResult = suspendCancellableCoroutine { cont ->
            textRecognizer.process(inputImage)
                .addOnSuccessListener { text -> cont.resume(text) }
                .addOnFailureListener { e -> cont.resumeWithException(e) }
        }

        // 3. Extract full text
        val ocrText = textResult.textBlocks.joinToString("\n") { it.text }

        // 4. Calculate average confidence (default 0.8f if unavailable)
        val confidences = textResult.textBlocks.mapNotNull { block ->
            // ML Kit Text.TextBlock does not expose confidence in all versions.
            // Use reflection or default value.
            try {
                val field = block.javaClass.getDeclaredField("confidence")
                field.isAccessible = true
                field.getFloat(block)
            } catch (_: Exception) {
                null
            }
        }
        val avgConfidence = if (confidences.isNotEmpty()) {
            confidences.average().toFloat()
        } else {
            DEFAULT_CONFIDENCE
        }

        // 5. Save original image to internal storage
        val timestamp = System.currentTimeMillis()
        val docsDir = File(context.filesDir, "documents").apply { mkdirs() }
        val imageFile = File(docsDir, "$timestamp.jpg")
        val fileSize = withContext(Dispatchers.IO) {
            context.contentResolver.openInputStream(imageUri)?.use { input ->
                FileOutputStream(imageFile).use { output ->
                    input.copyTo(output)
                }
            }
            imageFile.length()
        }

        // 6. Generate thumbnail (200x200 max, JPEG quality 60)
        val thumbDir = File(docsDir, "thumbs").apply { mkdirs() }
        val thumbFile = File(thumbDir, "$timestamp.jpg")
        withContext(Dispatchers.IO) {
            val original = BitmapFactory.decodeFile(imageFile.absolutePath) ?: return@withContext
            try {
                val maxDim = 200
                val ratio = minOf(
                    maxDim.toFloat() / original.width,
                    maxDim.toFloat() / original.height,
                )
                val width = (original.width * ratio).toInt().coerceAtLeast(1)
                val height = (original.height * ratio).toInt().coerceAtLeast(1)
                val thumbnail = Bitmap.createScaledBitmap(original, width, height, true)
                try {
                    FileOutputStream(thumbFile).use { out ->
                        thumbnail.compress(Bitmap.CompressFormat.JPEG, THUMB_QUALITY, out)
                    }
                } finally {
                    if (thumbnail !== original) thumbnail.recycle()
                }
            } finally {
                original.recycle()
            }
        }

        // 7. Auto-generate title
        val title = if (ocrText.isNotBlank()) {
            ocrText.lines().firstOrNull { it.isNotBlank() }
                ?.take(MAX_TITLE_LENGTH) ?: "Scan $timestamp"
        } else {
            "Scan $timestamp"
        }

        // 8. Compute SHA-256 content hash
        val contentHash = sha256(ocrText)

        // 9. Categorize
        val category = categorizer.categorize(ocrText)

        // 10. Create and insert entity
        val entity = ScannedDocumentEntity(
            title = title,
            ocrText = ocrText,
            category = category,
            imagePath = imageFile.absolutePath,
            thumbnailPath = thumbFile.absolutePath,
            fileSize = fileSize,
            ocrConfidence = avgConfidence,
            contentHash = contentHash,
            createdAt = timestamp,
            updatedAt = timestamp,
        )
        val id = documentDao.insert(entity)

        // 11. Return the entity with the generated id
        return entity.copy(id = id)
    }

    /** Delete a document from Room and remove its image files from disk. */
    suspend fun deleteDocument(doc: ScannedDocumentEntity) {
        documentDao.delete(doc)
        withContext(Dispatchers.IO) {
            try {
                File(doc.imagePath).delete()
            } catch (e: Exception) {
                Log.w(TAG, "Failed to delete image: ${e.message}")
            }
            try {
                File(doc.thumbnailPath).delete()
            } catch (e: Exception) {
                Log.w(TAG, "Failed to delete thumbnail: ${e.message}")
            }
        }
    }

    private fun sha256(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val bytes = digest.digest(input.toByteArray(Charsets.UTF_8))
        return bytes.joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val TAG = "DocumentScanner"
        private const val DEFAULT_CONFIDENCE = 0.8f
        private const val MAX_TITLE_LENGTH = 50
        private const val THUMB_QUALITY = 60
    }
}
