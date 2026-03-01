# Widget UX Improvements Design

**Date:** 2026-02-28
**Status:** Approved

## Problem

The desktop widget lacks visual feedback during processing, has no onboarding for new users, cold starts take ~20s, and learning features are invisible despite being fully wired.

## 1. Typing Indicator in Chat

Show animated "Jarvis is thinking..." dots in the chat area while processing, replaced by the actual response when ready.

- Insert a temporary chat bubble with 3 pulsing dots when `_widget_state` transitions to "processing"
- CSS keyframe animation: dots fade in/out sequentially (0.4s per dot, 1.2s cycle)
- Remove the indicator element and insert the real response when the `/command` HTTP call completes
- If the request errors, replace indicator with an error message styled in the error color (#d15a5a)
- The chat input should be disabled (greyed out) while the indicator is visible to prevent duplicate submissions

## 2. Help Button + Tooltip Hints

Add a `?` button in the widget header that opens a help overlay, plus hover tooltips on all interactive controls.

- `?` button in top-right of panel header, same styling as existing icon buttons
- Click opens a semi-transparent overlay listing: voice commands, text input, teach commands, keyboard shortcuts
- Tooltips on: mic button ("Click or say wake word"), text input ("Type a command or question"), send button ("Send command"), orb ("Click to show/hide panel")
- Tooltips use CSS `title` attribute or custom positioned div with 200ms hover delay
- Help overlay dismissed by clicking outside or pressing Escape

## 3. Cold Start Optimization

Two-pronged approach: pre-warm the CommandBus at service startup + lazy-load non-essential subsystems.

**Pre-warm at startup:**
- In `serve_mobile()`, call `_get_bus()` before entering the request loop so the first user request hits a warm cache
- In the widget, fire a background `/health` ping on startup to trigger mobile API warm-up
- Optionally warm the embedding model in a background thread after bus creation

**Lazy-load non-essentials in `create_app()`:**
- Harvesting (4 providers + BudgetManager): defer until first harvest command
- SyncEngine + crypto + triggers: defer until first sync request
- ProactiveEngine: defer until daemon loop starts
- Learning trackers (Preference, ResponseFeedback, UsagePattern): defer until first `learn_from_interaction()` call
- Use property-based lazy initialization pattern: store `None`, create on first access

**Target:** Cold start from ~20s to <8s, warm requests stay at ~3s.

## 4. Auto-Learn + Manual Teach Commands

Learning is already wired (auto-learn on every interaction). Add explicit user-facing commands and onboarding.

**Manual commands (natural language, routed by IntentClassifier):**
- "Remember that [fact]" — stores a fact in KG with high confidence + user source
- "Forget [topic]" — soft-deletes matching KG facts (marks as retracted, not hard delete)
- "What do you know about [topic]?" — queries KG + memory for all known facts about topic

**Onboarding welcome message:**
- On first widget connection (no prior chat history), display a welcome message:
  > "Hi! I'm Jarvis. You can ask me anything, teach me with 'Remember that...', or say 'Help' to see what I can do."
- Stored as a flag in widget localStorage so it only shows once

**Learning visibility:**
- After processing a command that triggers learning, show a subtle "Learned" indicator (small icon or text that fades after 2s)
- Help overlay includes a "Teaching Jarvis" section explaining remember/forget/what-do-you-know

## Implementation Priority

1. Typing indicator (highest user impact, blocks duplicate requests)
2. Cold start optimization (reduces frustration on first use)
3. Help + tooltips + onboarding (discoverability)
4. Manual teach commands (power user feature)
