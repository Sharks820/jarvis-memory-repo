package com.jarvis.assistant.ui.documents

import android.Manifest
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import java.io.File

/**
 * Camera viewfinder screen with a scan button. Captures an image, runs
 * ML Kit OCR, and shows a preview dialog with auto-categorization.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DocumentScannerScreen(
    onNavigateBack: () -> Unit = {},
    viewModel: DocumentViewModel = hiltViewModel(),
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    val isScanning by viewModel.isScanning.collectAsState()
    val scanResult by viewModel.scanResult.collectAsState()
    val scanError by viewModel.scanError.collectAsState()

    // Camera permission
    var hasCameraPermission by remember { mutableStateOf(false) }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        hasCameraPermission = granted
    }

    LaunchedEffect(Unit) {
        val result = ContextCompat.checkSelfPermission(
            context, Manifest.permission.CAMERA,
        )
        if (result == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            hasCameraPermission = true
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    // ImageCapture reference shared between preview and capture button
    var imageCapture by remember { mutableStateOf<ImageCapture?>(null) }

    Box(modifier = Modifier.fillMaxSize()) {
        if (!hasCameraPermission) {
            // Permission not granted
            Column(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    "Camera permission is required to scan documents.",
                    style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.padding(16.dp),
                )
                TextButton(onClick = {
                    permissionLauncher.launch(Manifest.permission.CAMERA)
                }) {
                    Text("Grant Permission")
                }
            }
        } else {
            // CameraX Preview
            AndroidView(
                factory = { ctx ->
                    PreviewView(ctx).also { previewView ->
                        val cameraProviderFuture = ProcessCameraProvider.getInstance(ctx)
                        cameraProviderFuture.addListener({
                            try {
                                val cameraProvider = cameraProviderFuture.get()
                                val preview = Preview.Builder().build().also {
                                    it.setSurfaceProvider(previewView.surfaceProvider)
                                }
                                val capture = ImageCapture.Builder()
                                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                                    .build()
                                imageCapture = capture

                                cameraProvider.unbindAll()
                                cameraProvider.bindToLifecycle(
                                    lifecycleOwner,
                                    CameraSelector.DEFAULT_BACK_CAMERA,
                                    preview,
                                    capture,
                                )
                            } catch (e: Exception) {
                                android.util.Log.e("DocScanner", "Camera init failed: ${e.message}")
                            }
                        }, ContextCompat.getMainExecutor(ctx))
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )

            // Scan FAB at bottom center
            if (!isScanning) {
                FloatingActionButton(
                    onClick = {
                        val capture = imageCapture ?: return@FloatingActionButton
                        val photoFile = File(
                            context.cacheDir,
                            "scan_${System.currentTimeMillis()}.jpg",
                        )
                        val outputOptions = ImageCapture.OutputFileOptions
                            .Builder(photoFile)
                            .build()

                        capture.takePicture(
                            outputOptions,
                            ContextCompat.getMainExecutor(context),
                            object : ImageCapture.OnImageSavedCallback {
                                override fun onImageSaved(
                                    output: ImageCapture.OutputFileResults,
                                ) {
                                    val uri = Uri.fromFile(photoFile)
                                    viewModel.scanDocument(uri)
                                    // Clean up temp file after scan to prevent cache leak
                                    photoFile.delete()
                                }

                                override fun onError(exception: ImageCaptureException) {
                                    viewModel.scanError.value =
                                        exception.message ?: "Failed to capture image"
                                    photoFile.delete()
                                }
                            },
                        )
                    },
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(bottom = 32.dp),
                ) {
                    Icon(Icons.Filled.CameraAlt, contentDescription = "Scan Document")
                }
            } else {
                // Loading indicator while scanning
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(16.dp),
                )
            }
        }

        // Scan result dialog
        scanResult?.let { doc ->
            var editedTitle by remember(doc.id) { mutableStateOf(doc.title) }
            var editedCategory by remember(doc.id) { mutableStateOf(doc.category) }

            AlertDialog(
                onDismissRequest = { viewModel.clearScanResult() },
                title = { Text("Scan Complete") },
                text = {
                    Column(Modifier.verticalScroll(rememberScrollState())) {
                        OutlinedTextField(
                            value = editedTitle,
                            onValueChange = { editedTitle = it },
                            label = { Text("Title") },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                        )
                        Spacer(Modifier.height(8.dp))

                        // Category chips
                        Text(
                            "Category",
                            style = MaterialTheme.typography.labelMedium,
                        )
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            viewModel.categories.forEach { cat ->
                                FilterChip(
                                    selected = editedCategory == cat,
                                    onClick = { editedCategory = cat },
                                    label = { Text(cat.replaceFirstChar { it.uppercase() }) },
                                )
                            }
                        }

                        Spacer(Modifier.height(8.dp))

                        // OCR text preview
                        Text(
                            "OCR Text",
                            style = MaterialTheme.typography.labelMedium,
                        )
                        val previewText = if (doc.ocrText.length > MAX_PREVIEW_CHARS) {
                            doc.ocrText.take(MAX_PREVIEW_CHARS) + "..."
                        } else {
                            doc.ocrText.ifBlank { "(No text detected)" }
                        }
                        Text(
                            previewText,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(top = 4.dp),
                        )

                        Spacer(Modifier.height(4.dp))
                        Text(
                            "Confidence: ${"%.0f".format(doc.ocrConfidence * 100)}%",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                confirmButton = {
                    TextButton(onClick = {
                        // Save any edits (title and category)
                        if (editedCategory != doc.category) {
                            viewModel.updateCategory(doc.id, editedCategory)
                        }
                        if (editedTitle != doc.title) {
                            viewModel.updateTitle(doc.id, editedTitle)
                        }
                        viewModel.clearScanResult()
                        onNavigateBack()
                    }) {
                        Text("Save")
                    }
                },
                dismissButton = {
                    TextButton(onClick = {
                        viewModel.deleteDocument(doc)
                        viewModel.clearScanResult()
                    }) {
                        Text("Retake")
                    }
                },
            )
        }

        // Error dialog
        if (scanError != null) {
            AlertDialog(
                onDismissRequest = { viewModel.clearScanResult() },
                title = { Text("Scan Error") },
                text = { Text(scanError ?: "Unknown error") },
                confirmButton = {
                    TextButton(onClick = { viewModel.clearScanResult() }) {
                        Text("OK")
                    }
                },
            )
        }
    }
}

private const val MAX_PREVIEW_CHARS = 200
