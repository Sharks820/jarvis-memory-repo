package com.jarvis.assistant.ui.chat

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.feature.voice.VoiceEngine
import com.jarvis.assistant.feature.voice.VoiceState
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

@Composable
fun ChatScreen(
    voiceEngine: VoiceEngine,
    viewModel: ChatViewModel = hiltViewModel(),
) {
    val messages by viewModel.messages.collectAsState()
    val input by viewModel.inputText.collectAsState()
    val isSending by viewModel.isSending.collectAsState()
    val voiceState by voiceEngine.state.collectAsState()
    val haptic = LocalHapticFeedback.current

    Column(Modifier.fillMaxSize()) {
        // Messages list (newest at bottom → reversed layout)
        LazyColumn(
            modifier = Modifier.weight(1f),
            reverseLayout = true,
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (messages.isEmpty()) {
                item {
                    Box(Modifier.fillParentMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            "Start a conversation with Jarvis",
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            } else {
                items(messages, key = { it.id }) { msg ->
                    ChatBubble(msg)
                }
            }
        }

        // Typing / processing indicator
        if (isSending || voiceState is VoiceState.Processing) {
            TypingIndicator()
        }

        // Voice state indicator
        if (voiceState is VoiceState.Listening || voiceState is VoiceState.Transcribing) {
            VoiceWaveIndicator(voiceState)
        }

        // Input bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { viewModel.inputText.value = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Message Jarvis...") },
                singleLine = true,
            )

            if (isSending) {
                CircularProgressIndicator(
                    modifier = Modifier.padding(start = 8.dp),
                    strokeWidth = 2.dp,
                )
            } else {
                IconButton(onClick = { viewModel.sendMessage() }) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send")
                }
            }

            val isListening = voiceState is VoiceState.Listening ||
                voiceState is VoiceState.Transcribing
            IconButton(
                onClick = {
                    haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                    if (isListening) {
                        voiceEngine.stopListening()
                    } else {
                        voiceEngine.startListening()
                    }
                },
            ) {
                Icon(
                    if (isListening) Icons.Filled.MicOff else Icons.Filled.Mic,
                    contentDescription = if (isListening) "Stop listening" else "Voice input",
                    tint = if (isListening)
                        MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurface,
                )
            }
        }
    }
}

/** Three pulsing dots that indicate Jarvis is thinking. */
@Composable
private fun TypingIndicator() {
    val transition = rememberInfiniteTransition(label = "typing")
    Row(
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        repeat(3) { index ->
            val scale by transition.animateFloat(
                initialValue = 0.5f,
                targetValue = 1.0f,
                animationSpec = infiniteRepeatable(
                    animation = tween(durationMillis = 600, easing = LinearEasing),
                    repeatMode = RepeatMode.Reverse,
                    initialStartOffset = androidx.compose.animation.core.StartOffset(
                        index * 200,
                    ),
                ),
                label = "dot_$index",
            )
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .scale(scale)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.7f)),
            )
        }
    }
}

/** Animated voice indicator replacing the static "Listening..." text. */
@Composable
private fun VoiceWaveIndicator(voiceState: VoiceState) {
    val transition = rememberInfiniteTransition(label = "voice_wave")
    Row(
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Show animated bars when listening
        if (voiceState is VoiceState.Listening) {
            repeat(5) { index ->
                val heightFraction by transition.animateFloat(
                    initialValue = 0.3f,
                    targetValue = 1.0f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(durationMillis = 400, easing = LinearEasing),
                        repeatMode = RepeatMode.Reverse,
                        initialStartOffset = androidx.compose.animation.core.StartOffset(
                            index * 100,
                        ),
                    ),
                    label = "bar_$index",
                )
                Box(
                    modifier = Modifier
                        .size(width = 4.dp, height = (20 * heightFraction).dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(MaterialTheme.colorScheme.primary),
                )
            }
        }

        // Show partial text when transcribing
        if (voiceState is VoiceState.Transcribing) {
            Text(
                voiceState.partialText,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun ChatBubble(msg: ConversationEntity) {
    val isUser = msg.role == "user"
    val screenWidth = LocalConfiguration.current.screenWidthDp.dp
    val maxBubbleWidth = screenWidth * 0.78f

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = maxBubbleWidth)
                .clip(
                    RoundedCornerShape(
                        topStart = 12.dp,
                        topEnd = 12.dp,
                        bottomStart = if (isUser) 12.dp else 2.dp,
                        bottomEnd = if (isUser) 2.dp else 12.dp,
                    ),
                )
                .background(
                    if (isUser) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.surfaceVariant,
                )
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            Text(
                msg.content,
                color = if (isUser) MaterialTheme.colorScheme.onPrimary
                else MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyLarge,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                formatTime(msg.createdAt),
                style = MaterialTheme.typography.labelSmall,
                color = if (isUser) MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.7f)
                else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
            )

            // Quick actions for assistant messages
            if (!isUser) {
                ResponseActions(msg.content)
            }
        }
    }
}

/** Quick action chips below assistant messages. */
@Composable
private fun ResponseActions(content: String) {
    if (content.isBlank()) return
    Spacer(Modifier.height(6.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        AssistChip(
            onClick = { /* copy to clipboard handled by system */ },
            label = { Text("Copy", style = MaterialTheme.typography.labelSmall) },
            colors = AssistChipDefaults.assistChipColors(
                containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.6f),
            ),
            border = null,
        )
    }
}

private val timeFormat = DateTimeFormatter.ofPattern("h:mm a", Locale.getDefault())
    .withZone(ZoneId.systemDefault())
private fun formatTime(millis: Long): String = timeFormat.format(Instant.ofEpochMilli(millis))
