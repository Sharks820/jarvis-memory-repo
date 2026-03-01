package com.jarvis.assistant.ui.documents

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Searchable document list with category filter chips, thumbnails,
 * and sync status indicators.
 */
@Composable
fun DocumentListScreen(
    onNavigateToScanner: () -> Unit = {},
    viewModel: DocumentViewModel = hiltViewModel(),
) {
    val allDocuments by viewModel.allDocuments.collectAsState()
    val searchResults by viewModel.searchResults.collectAsState()
    val selectedCategory by viewModel.selectedCategory.collectAsState()

    var searchQuery by remember { mutableStateOf("") }
    val isSearching = searchQuery.isNotBlank()
    val displayDocs = if (isSearching) searchResults else {
        if (selectedCategory != null) {
            allDocuments.filter { it.category == selectedCategory }
        } else {
            allDocuments
        }
    }

    // Debounced search
    LaunchedEffect(searchQuery) {
        if (searchQuery.isNotBlank()) {
            delay(SEARCH_DEBOUNCE_MS)
            viewModel.search(searchQuery)
        } else {
            viewModel.searchResults.value = emptyList()
        }
    }

    Scaffold(
        floatingActionButton = {
            FloatingActionButton(onClick = onNavigateToScanner) {
                Icon(Icons.Filled.CameraAlt, contentDescription = "Scan Document")
            }
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            // Search bar
            OutlinedTextField(
                value = searchQuery,
                onValueChange = { searchQuery = it },
                label = { Text("Search documents") },
                leadingIcon = {
                    Icon(Icons.Filled.Search, contentDescription = null)
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                singleLine = true,
            )

            // Category filter chips
            LazyRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.padding(horizontal = 16.dp),
            ) {
                item {
                    FilterChip(
                        selected = selectedCategory == null,
                        onClick = { viewModel.selectedCategory.value = null },
                        label = { Text("All") },
                    )
                }
                items(viewModel.categories) { category ->
                    FilterChip(
                        selected = selectedCategory == category,
                        onClick = {
                            viewModel.selectedCategory.value =
                                if (selectedCategory == category) null else category
                        },
                        label = { Text(categoryLabel(category)) },
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            if (displayDocs.isEmpty()) {
                // Empty state
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Filled.Description,
                            contentDescription = null,
                            modifier = Modifier.size(64.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(16.dp))
                        Text(
                            if (isSearching) {
                                "No documents match your search."
                            } else {
                                "No documents scanned yet. Tap the scan button to get started."
                            },
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            } else {
                // Document list
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(
                        items = displayDocs,
                        key = { it.id },
                    ) { doc ->
                        DocumentCard(doc)
                    }
                }
            }
        }
    }
}

@Composable
private fun DocumentCard(doc: ScannedDocumentEntity) {
    val dateStr = remember(doc.createdAt) {
        SimpleDateFormat("MMM d, yyyy", Locale.US).format(Date(doc.createdAt))
    }
    val thumbnail = remember(doc.thumbnailPath) {
        try {
            // Use inSampleSize to limit memory even if thumbnail is unexpectedly large
            val options = BitmapFactory.Options().apply { inSampleSize = 1 }
            // Decode bounds first to determine if downsampling is needed
            BitmapFactory.Options().apply { inJustDecodeBounds = true }.also {
                BitmapFactory.decodeFile(doc.thumbnailPath, it)
                // Target 120px for list thumbnails; downsample if larger
                val maxDim = maxOf(it.outWidth, it.outHeight)
                if (maxDim > 240) {
                    options.inSampleSize = maxDim / 120
                }
            }
            BitmapFactory.decodeFile(doc.thumbnailPath, options)?.asImageBitmap()
        } catch (_: Exception) {
            null
        } catch (_: OutOfMemoryError) {
            null
        }
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Thumbnail
            if (thumbnail != null) {
                Image(
                    bitmap = thumbnail,
                    contentDescription = "Document thumbnail",
                    modifier = Modifier.size(60.dp),
                    contentScale = ContentScale.Crop,
                )
            } else {
                Icon(
                    Icons.Filled.Description,
                    contentDescription = null,
                    modifier = Modifier.size(60.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.width(12.dp))

            // Document info
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    doc.title,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(2.dp))

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    SuggestionChip(
                        onClick = {},
                        label = {
                            Text(
                                categoryLabel(doc.category),
                                style = MaterialTheme.typography.labelSmall,
                            )
                        },
                    )
                    Text(
                        dateStr,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                Spacer(Modifier.height(2.dp))

                // OCR preview
                Text(
                    doc.ocrText.take(OCR_PREVIEW_LENGTH).replace("\n", " "),
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            // Sync status
            Icon(
                imageVector = if (doc.syncedToDesktop) {
                    Icons.Filled.CheckCircle
                } else {
                    Icons.Filled.CloudUpload
                },
                contentDescription = if (doc.syncedToDesktop) "Synced" else "Pending sync",
                tint = if (doc.syncedToDesktop) {
                    Color(0xFF4CAF50)
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
                modifier = Modifier.size(20.dp),
            )
        }
    }
}

private fun categoryLabel(category: String): String = when (category) {
    "receipt" -> "Receipt"
    "warranty" -> "Warranty"
    "id" -> "ID"
    "medical" -> "Medical"
    "insurance" -> "Insurance"
    else -> "Other"
}

private const val SEARCH_DEBOUNCE_MS = 500L
private const val OCR_PREVIEW_LENGTH = 100
