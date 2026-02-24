package com.jarvis.assistant.ui.documents

import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import com.jarvis.assistant.feature.documents.DocumentCategorizer
import com.jarvis.assistant.feature.documents.DocumentScanner
import com.jarvis.assistant.feature.documents.DocumentSearchEngine
import com.jarvis.assistant.feature.documents.DocumentSyncManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class DocumentViewModel @Inject constructor(
    private val documentDao: DocumentDao,
    private val documentScanner: DocumentScanner,
    private val searchEngine: DocumentSearchEngine,
    private val syncManager: DocumentSyncManager,
    private val categorizer: DocumentCategorizer,
) : ViewModel() {

    val allDocuments: StateFlow<List<ScannedDocumentEntity>> =
        documentDao.getAllFlow()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val documentCount: StateFlow<Int> =
        documentDao.getCountFlow()
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)

    val searchResults = MutableStateFlow<List<ScannedDocumentEntity>>(emptyList())
    val selectedCategory = MutableStateFlow<String?>(null)
    val isScanning = MutableStateFlow(false)
    val scanResult = MutableStateFlow<ScannedDocumentEntity?>(null)
    val scanError = MutableStateFlow<String?>(null)

    val categories: List<String> = categorizer.getAllCategories()

    /** Scan a document from the given image URI. */
    fun scanDocument(imageUri: Uri) {
        viewModelScope.launch {
            isScanning.value = true
            scanError.value = null
            try {
                val result = documentScanner.scanAndExtract(imageUri)
                scanResult.value = result
                // Attempt immediate sync
                try {
                    syncManager.syncDocument(result)
                } catch (_: Exception) {
                    // Sync is best-effort; will retry in background
                }
            } catch (e: Exception) {
                scanError.value = e.message ?: "Scan failed"
            } finally {
                isScanning.value = false
            }
        }
    }

    /** Search documents by query text. */
    fun search(query: String) {
        viewModelScope.launch {
            try {
                val results = searchEngine.search(query, selectedCategory.value)
                searchResults.value = results
            } catch (_: Exception) {
                searchResults.value = emptyList()
            }
        }
    }

    /** Update the category of a document. */
    fun updateCategory(docId: Long, category: String) {
        viewModelScope.launch {
            val doc = documentDao.getById(docId) ?: return@launch
            documentDao.update(
                doc.copy(category = category, updatedAt = System.currentTimeMillis()),
            )
        }
    }

    /** Delete a document and its associated files. */
    fun deleteDocument(doc: ScannedDocumentEntity) {
        viewModelScope.launch {
            documentScanner.deleteDocument(doc)
        }
    }

    /** Sync all pending documents to the desktop brain. */
    fun syncPendingDocuments() {
        viewModelScope.launch {
            try {
                syncManager.syncPending()
            } catch (_: Exception) {
                // Best-effort
            }
        }
    }

    /** Clear the last scan result (after user dismisses preview). */
    fun clearScanResult() {
        scanResult.value = null
        scanError.value = null
    }
}
