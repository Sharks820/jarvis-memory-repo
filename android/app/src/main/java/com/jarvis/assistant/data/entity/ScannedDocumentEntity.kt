package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "scanned_documents")
data class ScannedDocumentEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val title: String,
    val ocrText: String,
    val category: String,
    val imagePath: String,
    val thumbnailPath: String,
    val fileSize: Long,
    val ocrConfidence: Float,
    val syncedToDesktop: Boolean = false,
    val contentHash: String,
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
)
