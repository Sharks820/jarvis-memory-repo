package com.jarvis.assistant.data.dao

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface DocumentDao {

    @Insert
    suspend fun insert(doc: ScannedDocumentEntity): Long

    @Update
    suspend fun update(doc: ScannedDocumentEntity)

    @Delete
    suspend fun delete(doc: ScannedDocumentEntity)

    @Query("SELECT * FROM scanned_documents ORDER BY createdAt DESC")
    fun getAllFlow(): Flow<List<ScannedDocumentEntity>>

    @Query("SELECT * FROM scanned_documents WHERE category = :category ORDER BY createdAt DESC")
    fun getByCategoryFlow(category: String): Flow<List<ScannedDocumentEntity>>

    @Query(
        "SELECT * FROM scanned_documents WHERE ocrText LIKE '%' || :query || '%' ORDER BY createdAt DESC",
    )
    suspend fun searchByContent(query: String): List<ScannedDocumentEntity>

    @Query(
        "SELECT * FROM scanned_documents WHERE ocrText LIKE '%' || :query || '%' " +
            "AND category = :category ORDER BY createdAt DESC",
    )
    suspend fun searchByContentAndCategory(
        query: String,
        category: String,
    ): List<ScannedDocumentEntity>

    @Query("SELECT * FROM scanned_documents WHERE id = :id")
    suspend fun getById(id: Long): ScannedDocumentEntity?

    @Query("SELECT COUNT(*) FROM scanned_documents")
    fun getCountFlow(): Flow<Int>

    @Query("SELECT * FROM scanned_documents WHERE syncedToDesktop = 0")
    suspend fun getUnsyncedDocuments(): List<ScannedDocumentEntity>

    @Query("UPDATE scanned_documents SET syncedToDesktop = 1 WHERE id = :id")
    suspend fun markSynced(id: Long)

    /** Get recently scanned documents (for on-device intelligence context). */
    @Query("SELECT * FROM scanned_documents ORDER BY createdAt DESC LIMIT :limit")
    suspend fun getRecentDocuments(limit: Int = 5): List<ScannedDocumentEntity>
}
