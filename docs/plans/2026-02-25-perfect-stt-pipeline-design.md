# Perfect STT Pipeline Design

## Problem

Current STT pipeline has ZERO post-processing. Raw Whisper output goes directly to command processing with only `.strip()`. This causes:
- Misheard words and names
- Missing/wrong punctuation and capitalization
- Filler words polluting memory and LLM context
- Whisper hallucinations on silence/noise
- Local backend confidence metric is wrong (uses `language_probability` instead of logprobs)
- No audio preprocessing (noise, normalization)

## Design: Full Quality Pipeline

```
Audio (float32, 16kHz)
  |
  v
[1] Audio Preprocessing
  -> Peak normalize to -3dBFS
  -> HPSS: separate speech harmonics from percussive noise (keyboard, coughs)
  -> Spectral noise reduction (noisereduce)
  -> Silence trimming (-30dB threshold)
  |
  v
[2] Whisper Transcription (tuned params)
  -> beam_size=5
  -> no_repeat_ngram_size=3
  -> hallucination_silence_threshold=0.2
  -> condition_on_previous_text=False
  -> word_timestamps=True (enables per-word confidence)
  -> initial_prompt=JARVIS_DEFAULT_PROMPT
  |
  v
[3] Word-Level Re-decode (suspect spans)
  -> For each word with logprob < -1.5 AND duration < 120ms:
     -> Re-decode that span with beam=10 and 200ms extra context
     -> Accept if new logprob is higher
  |
  v
[4] Hallucination Detection
  -> Known hallucination phrases: "Thanks for watching", "Subscribe", "[music]", etc.
  -> Repeated 3+ word sequences (regex)
  -> Compression ratio > 2.4 flagged
  -> Return empty if all text is hallucinated
  |
  v
[5] Filler Word Removal (smart)
  -> Remove: "um", "uh", "er", "ah"
  -> Context-aware "like" removal (filler vs quotative)
  -> Remove: "you know", "I mean" when used as filler
  -> Preserve sentence structure
  |
  v
[6] LLM Post-Correction
  -> System prompt with personal vocabulary
  -> Fix: spelling, punctuation, capitalization, entity names
  -> Uses fast model (Kimi K2 via Groq) for low latency
  -> Skip for short commands (< 5 words) with high confidence (> 0.95)
  |
  v
[7] NER Entity Correction
  -> Match transcribed names against personal contact/entity list
  -> Phonetic similarity matching for near-misses
  -> Correct to canonical spelling
  |
  v
Clean text output
```

## New Files

### `engine/src/jarvis_engine/stt_postprocess.py`
All post-processing logic:
- `preprocess_audio(audio)` - normalize, HPSS, noise reduce, silence trim
- `detect_hallucination(text, segments)` - known phrases, repetition, compression ratio
- `remove_fillers(text)` - smart filler word removal
- `correct_with_llm(text, gateway, vocab)` - LLM post-correction
- `correct_entities(text, entity_list)` - NER-based entity correction
- `postprocess_transcription(result, gateway, vocab, entity_list)` - full pipeline

### `engine/src/jarvis_engine/data/personal_vocab.txt`
Flat text file with personal vocabulary:
```
Conner (not Connor, Conor)
Jarvis (AI assistant name)
ops brief, knowledge graph, proactive engine
Ollama, Groq, Anthropic, SQLite
```

## Modified Files

### `engine/src/jarvis_engine/stt.py`
- `SpeechToText.transcribe_audio()`: add word_timestamps, condition_on_previous_text=False, beam_size=5, no_repeat_ngram_size=3, hallucination_silence_threshold=0.2
- Fix local confidence: compute from segment avg_logprob instead of language_probability
- `transcribe_smart()`: call `preprocess_audio()` before transcription, call `postprocess_transcription()` after

### `engine/tests/test_stt.py`
- Update mock assertions for new Whisper parameters
- Add tests for each postprocessing function

## Confidence Fix Detail

Current (WRONG):
```python
confidence = getattr(info, "language_probability", 0.0)  # Always ~1.0 for English
```

Fixed (CORRECT):
```python
logprobs = [seg.avg_logprob for seg in segments if hasattr(seg, 'avg_logprob')]
if logprobs:
    avg_logprob = sum(logprobs) / len(logprobs)
    confidence = min(1.0, max(0.0, 1.0 + avg_logprob))
else:
    confidence = getattr(info, "language_probability", 0.0)
```

## Dependencies

- `noisereduce` - spectral noise reduction (pip install noisereduce)
- `librosa` - HPSS audio separation (pip install librosa)
- No new heavy deps (both are lightweight numpy-based)

## Latency Budget

| Stage | Latency | Notes |
|-------|---------|-------|
| Audio preprocessing | ~50ms | numpy/librosa ops |
| Whisper transcription | 500-2000ms | Existing, slightly slower with beam=5 |
| Word re-decode | 0-200ms | Only on suspect words (rare) |
| Hallucination detection | <5ms | Regex + string ops |
| Filler removal | <5ms | Regex + string ops |
| LLM post-correction | 500-1500ms | Groq Kimi K2 (~200 tok/s) |
| NER entity correction | <10ms | Dictionary lookup |
| **Total** | **~1.5-4s** | Quality over speed |

## Skip Path (Fast Commands)

For short commands (< 5 words) with high Whisper confidence (> 0.95) that match known command patterns (e.g., "pause jarvis", "brain status"), skip stages 6-7 (LLM + NER) entirely. This keeps voice commands snappy while ensuring longer speech gets full correction.

## Consulted Sources

Design validated by querying 4 AI models (Kimi K2, Mistral Large, Llama 3.3 70B, Qwen 3 32B) plus 3 research agents covering web search, academic papers, and provider documentation. Key innovations incorporated:
- Word-level re-decode on suspect spans (Kimi K2)
- HPSS audio separation (Mistral Large)
- NER entity correction (Llama 3.3 70B)
- Smart filler removal preserving discourse markers (Kimi K2)
