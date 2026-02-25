package com.jarvis.assistant.feature.documents

import android.util.Log
import com.jarvis.assistant.api.JarvisApiClient
import com.jarvis.assistant.api.models.CommandRequest
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Syncs scanned document OCR text and metadata to the desktop brain
 * via the /command endpoint. Only OCR text and metadata sync -- image
 * binaries stay on the phone.
 */
@Singleton
class DocumentSyncManager @Inject constructor(
    private val documentDao: DocumentDao,
    private val apiClient: JarvisApiClient,
) {

    private val dateFormat = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
        .withZone(ZoneId.systemDefault())

    /** Sync all unsynced documents to the desktop brain. */
    suspend fun syncPending() {
        val unsynced = documentDao.getUnsyncedDocuments()
        if (unsynced.isEmpty()) return

        Log.i(TAG, "Syncing ${unsynced.size} pending document(s) to desktop")
        for (doc in unsynced) {
            try {
                syncDocument(doc)
            } catch (e: Exception) {
                Log.w(TAG, "Failed to sync document ${doc.id}: ${e.message}")
                // Skip -- will retry next cycle
            }
        }
    }

    /** Sync a single document immediately. */
    suspend fun syncDocument(doc: ScannedDocumentEntity) {
        val dateStr = dateFormat.format(Instant.ofEpochMilli(doc.createdAt))
        // Truncate OCR text to 5000 chars for /command endpoint practical limits
        val truncatedOcr = if (doc.ocrText.length > MAX_OCR_SYNC_LENGTH) {
            doc.ocrText.take(MAX_OCR_SYNC_LENGTH) + "... [truncated]"
        } else {
            doc.ocrText
        }

        val command = buildString {
            append("Jarvis, store document: ")
            append("title=${doc.title}, ")
            append("category=${doc.category}, ")
            append("date=$dateStr, ")
            append("content=$truncatedOcr")
        }

        val response = apiClient.api().sendCommand(
            CommandRequest(text = command, execute = false),
        )

        if (response.ok) {
            documentDao.markSynced(doc.id)
            Log.i(TAG, "Document ${doc.id} synced successfully")
        } else {
            Log.w(TAG, "Desktop rejected document ${doc.id} sync")
        }
    }

    companion object {
        private const val TAG = "DocumentSyncManager"
        private const val MAX_OCR_SYNC_LENGTH = 5000
    }
}
