# Mobile On-Device LLM Research — March 2026

## Executive Summary

On-device LLMs have matured dramatically by March 2026. Flagship phones can now run 1B-4B parameter models at 20-80+ tokens/second with acceptable quality for intent routing, classification, simple Q&A, and command parsing. The key enablers are INT4 quantization, NPU acceleration (Apple Neural Engine 35 TOPS, Qualcomm Hexagon NPU 45+ TOPS), and purpose-built mobile inference frameworks (ExecuTorch, MediaPipe, MLC-LLM, llama.cpp).

**Top recommendation for Jarvis mobile fallback: Gemma 3n E2B (cross-platform) or Qwen3 0.6B (ultra-lightweight) with llama.cpp/MediaPipe, plus Gemma 3 270M as a micro-classifier.**

---

## 1. Target Hardware Profiles

### iPhone 16 Pro / Pro Max
| Spec | Value |
|------|-------|
| Chip | A18 Pro |
| RAM | 8 GB (OS reserves ~3-4 GB → ~4-5 GB available) |
| Neural Engine | 35 TOPS |
| GPU | 6-core Apple GPU (Metal 3) |
| Memory Bandwidth | ~100 GB/s (unified) |
| Key Frameworks | Core ML (→ Core AI in iOS 27), ExecuTorch, llama.cpp (Metal), MLX |

### Samsung Galaxy S25 Ultra
| Spec | Value |
|------|-------|
| Chip | Snapdragon 8 Elite |
| RAM | 12 GB (OS reserves ~4-5 GB → ~7-8 GB available) |
| NPU | Hexagon NPU (45+ TOPS) |
| GPU | Adreno 830 (Vulkan, OpenCL) |
| Memory Bandwidth | ~68 GB/s |
| Key Frameworks | MediaPipe, MLC-LLM (OpenCL/Vulkan), llama.cpp, ONNX Runtime, LiteRT/QNN |

---

## 2. Viable Models — Detailed Assessment

### Tier 1: Ultra-Lightweight (≤ 500M params) — Intent Routing & Classification

#### Gemma 3 270M
| Attribute | Detail |
|-----------|--------|
| Parameters | 270M |
| Quantized Size | ~125 MB (INT4) |
| RAM Required | ~125 MB active |
| iPhone 16 Pro Speed | ~20-35 tok/s (CPU), higher with Neural Engine |
| S25 Ultra Speed | ~30-50 tok/s estimated |
| Quality | IFEval 51.2%, strong instruction-following for its size |
| Battery Impact | **0.75% per 25 conversations** (Pixel 9 Pro benchmark) — exceptional |
| Context Window | 32K tokens |
| Frameworks | MediaPipe, LiteRT, llama.cpp, Ollama |
| License | Apache 2.0 (Gemma terms) |
| Best For | Intent classification, routing, entity extraction, text classification |
| Limitations | Weak at complex reasoning, multi-step tasks |

#### Qwen3 0.6B
| Attribute | Detail |
|-----------|--------|
| Parameters | 600M |
| Quantized Size | ~350-400 MB (INT4) |
| RAM Required | ~500 MB |
| iPhone 16 Pro Speed | ~40 tok/s (ExecuTorch) |
| S25 Ultra Speed | ~35-50 tok/s |
| Quality | Outperforms Llama 3.2 1B on tool calling (84% vs 55% benchmark) |
| Battery Impact | Low — sub-1B model |
| Context Window | 32K tokens |
| Frameworks | llama.cpp (GGUF), ExecuTorch, MediaPipe |
| License | Apache 2.0 |
| Best For | Tool calling, intent routing, simple Q&A, hybrid think/no-think modes |
| Limitations | Limited complex reasoning |

#### Qwen3.5-0.8B (NEW — March 2026)
| Attribute | Detail |
|-----------|--------|
| Parameters | 800M |
| Quantized Size | ~500 MB (INT4) |
| RAM Required | ~600 MB |
| Quality | Natively multimodal (text + image + video), 201 languages |
| Context Window | 262K tokens |
| Frameworks | llama.cpp, ExecuTorch |
| License | Apache 2.0 |
| Best For | Multimodal routing, multilingual commands |

### Tier 2: Lightweight (1B-2B params) — Capable On-Device Brain

#### Gemma 3n E2B ⭐ RECOMMENDED
| Attribute | Detail |
|-----------|--------|
| Parameters | ~2B effective (5-8B total with selective activation) |
| Quantized Size | ~1.0-1.5 GB |
| RAM Required | ~2 GB |
| iPhone 16 Pro Speed | ~620 tok/s prefill, ~30-50 tok/s decode (estimated) |
| S25 Ultra Speed | ~1600+ tok/s prefill on GPU, ~40-60 tok/s decode |
| Quality | LMArena > 1200, multimodal (text + image + audio) |
| Battery Impact | Very low due to MatFormer selective activation |
| Context Window | 32K tokens |
| Frameworks | MediaPipe, LiteRT, Google AI Edge |
| License | Gemma license (permissive, commercial OK) |
| Best For | **Full on-device assistant brain** — Q&A, classification, vision, audio |
| Key Innovation | MatFormer architecture activates only needed parameters → 2B memory for 5-8B quality |

#### Gemma 3 1B
| Attribute | Detail |
|-----------|--------|
| Parameters | 1B |
| Quantized Size | ~529 MB |
| RAM Required | ~1 GB |
| iPhone 16 Pro Speed | Up to **2585 tok/s** (Google benchmark, likely prefill) |
| S25 Ultra Speed | Comparable high speed with NPU |
| Quality | Good for text tasks, weaker on complex reasoning |
| Battery Impact | Very low |
| Context Window | 32K tokens |
| Frameworks | MediaPipe, AI Edge, llama.cpp |
| License | Gemma license |
| Best For | Smart replies, summarization, text classification |

#### MobileLLM-Pro 1B (Meta)
| Attribute | Detail |
|-----------|--------|
| Parameters | 1.084B |
| Quantized Size | ~600 MB (INT4) |
| RAM Required | ~1 GB |
| Quality | SOTA across 11 benchmarks, beats Gemma 3-1B and Llama 3.2-1B |
| Context Window | **128K tokens** — best-in-class for 1B |
| Key Innovation | Implicit positional distillation, interleaved local-global attention |
| Frameworks | ExecuTorch |
| License | Meta community license |
| Best For | Long-context tasks, mobile-first deployment |

#### SmolLM2 1.7B
| Attribute | Detail |
|-----------|--------|
| Parameters | 1.7B |
| Quantized Size | ~1 GB (INT4) |
| RAM Required | ~1.5 GB |
| Quality | Outperforms Llama 3.2-1B across multiple benchmarks |
| Frameworks | llama.cpp (GGUF), ExecuTorch |
| License | Apache 2.0 |
| Best For | General chat, summarization, coding assistance |

#### Llama 3.2 1B
| Attribute | Detail |
|-----------|--------|
| Parameters | 1B |
| Quantized Size | ~600 MB (INT4) |
| RAM Required | ~1 GB |
| iPhone 16 Pro Speed | ~40 tok/s (ExecuTorch + XNNPACK) |
| Quality | MMLU 49.3%, good for simple tasks |
| Context Window | 128K tokens |
| Frameworks | ExecuTorch (official), llama.cpp, MediaPipe |
| License | Meta Llama 3.2 Community License |
| Best For | Simple chat, basic Q&A |

### Tier 3: Medium (3B-4B params) — Maximum Quality Within Mobile RAM

#### Gemma 3n E4B
| Attribute | Detail |
|-----------|--------|
| Parameters | ~4B effective (larger MatFormer) |
| RAM Required | ~3 GB |
| Quality | LMArena > 1300, excellent reasoning |
| Frameworks | MediaPipe, LiteRT |
| License | Gemma license |
| Best For | High-quality on-device assistant (S25 Ultra preferred) |
| Note | Fits iPhone 16 Pro but leaves less headroom |

#### Llama 3.2 3B
| Attribute | Detail |
|-----------|--------|
| Parameters | 3B |
| Quantized Size | ~1.8 GB (INT4) |
| RAM Required | ~2-3 GB |
| iPhone 16 Pro Speed | ~15-25 tok/s |
| S25 Ultra Speed | ~20-35 tok/s |
| Quality | MMLU 63.4%, strong general reasoning |
| Context Window | 128K tokens |
| Frameworks | ExecuTorch, llama.cpp, MediaPipe |
| License | Meta Llama 3.2 Community License |
| Best For | General assistant, summarization, Q&A |

#### Phi-4-mini 3.8B
| Attribute | Detail |
|-----------|--------|
| Parameters | 3.8B |
| Quantized Size | ~2.2 GB (INT4) |
| RAM Required | ~3 GB |
| Quality | MMLU 84.8% (14B variant), strong math/code/reasoning |
| Context Window | 128K tokens |
| Frameworks | ONNX Runtime, llama.cpp, Ollama |
| License | MIT |
| Best For | Math, coding, structured reasoning |
| Note | Tight fit on iPhone (8GB total), comfortable on S25 Ultra (12GB) |

#### Phi-4-mini-flash-reasoning 3.8B (NEW)
| Attribute | Detail |
|-----------|--------|
| Parameters | 3.8B |
| Key Innovation | SambaY decoder, Gated Memory Units replacing attention |
| Performance | 10× throughput gain, 2-3× latency reduction vs standard Phi-4-mini |
| Battery | Up to 40% savings |
| Context Window | 64K tokens |
| License | MIT |
| Best For | Fast reasoning on device with minimal battery drain |

#### Qwen 2.5 3B
| Attribute | Detail |
|-----------|--------|
| Parameters | 3B |
| Quantized Size | ~1.8 GB (INT4) |
| RAM Required | ~2-3 GB |
| Quality | Strong coding, math, multilingual |
| Frameworks | llama.cpp, ExecuTorch, ONNX Runtime |
| License | Apache 2.0 |
| Best For | Multilingual assistant, code help |

#### Jamba Reasoning 3B
| Attribute | Detail |
|-----------|--------|
| Parameters | 3B |
| Key Innovation | SSM-Transformer hybrid → 2-5× efficiency vs pure Transformer |
| Context Window | **256K tokens** (1M token theoretical limit) |
| Quality | Strong on legal/medical document processing |
| License | Apache 2.0 |
| Best For | Long document processing, specialized reasoning |

### Tier 4: Stretch (7B+) — S25 Ultra Only, Aggressive Quantization

#### Qwen3.5 9B (via MLX on iPhone 16 Pro Max)
| Attribute | Detail |
|-----------|--------|
| Parameters | 9B |
| RAM Required | ~5-6 GB (4-bit) |
| Speed | Slower (~5-10 tok/s) but highest quality |
| Note | Only viable on iPhone 16 Pro Max with MLX; leaves little headroom |
| License | Apache 2.0 |

---

## 3. Deployment Framework Comparison

| Framework | iOS | Android | NPU Support | Model Format | LLM Optimized | Maturity |
|-----------|-----|---------|-------------|-------------|---------------|----------|
| **ExecuTorch** | ✅ (XNNPACK, CoreML, MPS) | ✅ (XNNPACK, QNN, Vulkan) | ✅ CoreML + QNN | .pte | ✅ (Llama focus) | Stable (1.0 Jan 2026) |
| **MediaPipe / LiteRT** | ✅ | ✅ | ✅ via QNN Accelerator | .tflite / .task | ✅ (Gemma focus) | Production |
| **llama.cpp** | ✅ (Metal) | ✅ (OpenCL, Vulkan) | ⚠️ CPU/GPU only | .gguf | ✅ | Very mature |
| **MLC-LLM** | ✅ (Metal) | ✅ (OpenCL, Vulkan) | ⚠️ GPU only | Custom | ✅ | Stable |
| **ONNX Runtime** | ✅ | ✅ | ✅ via providers | .onnx | ⚠️ Improving | Mature |
| **Apple MLX** | ✅ (Apple only) | ❌ | ✅ Neural Engine | MLX format | ✅ | Good |
| **Core ML / Core AI** | ✅ (Apple only) | ❌ | ✅ Neural Engine | .mlmodelc | ✅ | Production |

### Framework Recommendations:
- **Cross-platform (Jarvis)**: **llama.cpp** (GGUF format) — widest model support, works on both platforms, mature
- **Android-specific**: **MediaPipe** with LiteRT — best Gemma integration, NPU support via QNN
- **iOS-specific**: **ExecuTorch** or **Core ML** — best Neural Engine utilization
- **For Jarvis**: Start with **llama.cpp** for cross-platform, add MediaPipe for Android NPU acceleration as optimization

---

## 4. Platform-Specific Rankings

### Best for iPhone 16 Pro (8 GB RAM, ~4-5 GB available)

| Rank | Model | Size (Q4) | Speed | Quality | Why |
|------|-------|-----------|-------|---------|-----|
| 🥇 | **Gemma 3n E2B** | ~1.5 GB | ~30-50 tok/s | Excellent | Best quality/efficiency ratio, multimodal |
| 🥈 | **MobileLLM-Pro 1B** | ~600 MB | ~40+ tok/s | Very good | 128K context, SOTA benchmarks for 1B |
| 🥉 | **Qwen3 0.6B** | ~400 MB | ~40 tok/s | Good | Ultra-light, great tool calling |
| 4 | **Llama 3.2 3B** | ~1.8 GB | ~15-25 tok/s | Good+ | Max quality that fits comfortably |
| 5 | **Gemma 3 270M** | ~125 MB | ~35 tok/s | Basic | Micro-classifier, negligible battery |

### Best for Samsung Galaxy S25 Ultra (12 GB RAM, ~7-8 GB available)

| Rank | Model | Size (Q4) | Speed | Quality | Why |
|------|-------|-----------|-------|---------|-----|
| 🥇 | **Gemma 3n E4B** | ~3 GB | ~40-60 tok/s | Excellent+ | Best quality feasible, multimodal |
| 🥈 | **Phi-4-mini-flash 3.8B** | ~2.2 GB | ~30-50 tok/s | Excellent | Best reasoning, 40% battery savings |
| 🥉 | **Gemma 3n E2B** | ~1.5 GB | ~40-60 tok/s | Excellent | Leaves headroom for other apps |
| 4 | **Llama 3.2 3B** | ~1.8 GB | ~20-35 tok/s | Good+ | Well-tested ecosystem |
| 5 | **Qwen 2.5 3B** | ~1.8 GB | ~25-40 tok/s | Good+ | Best multilingual |

### Best Cross-Platform (Both devices)

| Rank | Model | Why |
|------|-------|-----|
| 🥇 | **Gemma 3n E2B** | Fits both, multimodal, excellent quality, Google ecosystem |
| 🥈 | **Qwen3 0.6B** | Ultra-light, fits any device, Apache 2.0, great tool calling |
| 🥉 | **Llama 3.2 1B** | Well-tested ExecuTorch path for both iOS & Android |
| 4 | **Gemma 3 270M** | Micro-model for classification/routing only |
| 5 | **SmolLM2 1.7B** | Good middle ground, Apache 2.0 |

---

## 5. Multi-Model Architecture for Jarvis

### Recommended Tiered Approach

```
┌─────────────────────────────────────────────────┐
│           JARVIS MOBILE AI STACK                 │
├─────────────────────────────────────────────────┤
│                                                  │
│  Layer 0: ROUTER (always loaded)                 │
│  ├── Gemma 3 270M (~125 MB)                     │
│  ├── Intent classification                       │
│  ├── Route to: local model / desktop / skip      │
│  └── Battery: negligible                         │
│                                                  │
│  Layer 1: ON-DEVICE BRAIN (loaded on demand)     │
│  ├── Gemma 3n E2B (~1.5 GB)                     │
│  ├── Q&A, summarization, learning dispatch       │
│  ├── Simple commands, smart replies              │
│  └── Unloaded after 30s idle to free RAM         │
│                                                  │
│  Layer 2: DESKTOP FALLBACK                       │
│  ├── Send to desktop when available              │
│  ├── Complex reasoning, knowledge graph queries  │
│  └── Long conversations, multi-step planning     │
│                                                  │
│  Layer 3: CLOUD BURST (optional)                 │
│  ├── Anthropic/Gemini API when desktop offline   │
│  ├── Privacy-filtered queries only               │
│  └── Rate-limited to control costs               │
│                                                  │
└─────────────────────────────────────────────────┘
```

### How This Works for Jarvis Use Cases:
| Use Case | Layer | Model | Expected Latency |
|----------|-------|-------|-----------------|
| "Set alarm for 7am" | 0→Command | Gemma 270M routes to local action | <200ms |
| "What's on my calendar?" | 0→1 | E2B reads local calendar data | <1s |
| "Summarize this email" | 0→1 | E2B summarizes locally | 1-3s |
| "Should I sell AAPL?" | 0→2/3 | Routes to desktop or cloud (complex) | 2-10s |
| "Remember I like Thai food" | 0→1→Queue | E2B processes, queues for desktop memory | <1s |
| Intent classification | 0 | Gemma 270M classifies intent | <100ms |
| Offline basic assistant | 0→1 | Full local stack, no network needed | <2s |

---

## 6. On-Device Learning, LoRA, and Knowledge Distillation

### Can Models Do Incremental Learning On-Device?

**Yes, but with significant caveats:**

1. **MobileFineTuner** (2026) — open-source framework enabling full fine-tuning and parameter-efficient fine-tuning (PEFT) directly on Android phones. Supports GPT-2, Gemma 3, Qwen 2.5. Uses parameter sharding, gradient accumulation, and energy-aware scheduling.

2. **Practical limits**: Fine-tuning a 1B model on-device requires:
   - 8+ GB RAM
   - Significant battery drain
   - Minutes to hours for meaningful adaptation
   - Best done during charging/idle

3. **Recommended approach for Jarvis**: Don't fine-tune on-device. Instead:
   - Collect preference signals on-device (what user liked/disliked)
   - Sync to desktop for LoRA fine-tuning
   - Push updated LoRA adapter weights back to phone (~1-10 MB)

### LoRA Adapters on Mobile — State of the Art

**Fully supported in 2026:**

| Framework | LoRA Support | Hot-Swap | Notes |
|-----------|-------------|----------|-------|
| MediaPipe | ✅ | ✅ | Native LoRA weight loading for Gemma |
| ExecuTorch | ✅ | ✅ | QAT + LoRA export pipeline |
| llama.cpp | ✅ | ✅ | Load LoRA adapters at runtime |
| MLC-LLM | ⚠️ | ❌ | Limited support |

**Key findings:**
- LoRA adapters are tiny (1-10 MB) and can be hot-swapped without reloading base model
- **CoA-LoRA** (2026 paper) enables configuration-aware adapters that adapt to different quantization levels automatically
- Google's **FunctionGemma** example: LoRA fine-tuned Gemma 270M for mobile tool-calling actions
- Unsloth workflow: Fine-tune with QAT → export .pte → deploy on phone at ~40 tok/s

**For Jarvis:**
- Train LoRA adapters on desktop (using conversation history, user preferences)
- Deploy to phone via sync mechanism (already have encrypted Fernet sync)
- Adapters: personal style adapter, domain adapter (work/home), command adapter

### Knowledge Distillation: Cloud → On-Device

**Mature and practical in 2026:**

1. **Task-aware distillation**: Train small student model to mimic large teacher on specific tasks
   - DeepSeek-R1 distilled models outperform RL-trained models in reasoning
   - Qwen 2.5 distilled variants (DistilQwen2.5) available for edge

2. **Iterative layer-wise distillation**: Remove layers from large model while preserving quality
   - Qwen2.5-3B: 36→28 layers with only 9.7% performance loss

3. **For Jarvis strategy:**
   - Use desktop Ollama/Anthropic as teacher
   - Periodically distill Jarvis-specific knowledge into LoRA adapters
   - Focus distillation on: Conner's command patterns, common intents, personal knowledge
   - Not real-time — batch process during desktop idle time

---

## 7. Apple Intelligence & Samsung Galaxy AI — Under the Hood

### Apple Intelligence Architecture (2026)

- **On-Device Model**: ~3B parameter Foundation Model (AFM)
  - Mixed 2-bit and 4-bit quantization via LoRA
  - Runs on A17 Pro+ Neural Engine
  - KV-cache sharing for memory efficiency
  - Handles ~80% of requests locally
  
- **Routing**: Three-tier system:
  1. **Tier 1 (On-Device)**: Summaries, notifications, simple rewrites
  2. **Tier 2 (Private Cloud Compute)**: Complex queries, encrypted processing, no data retention
  3. **Tier 3 (ChatGPT/External)**: Broad knowledge queries, requires user consent

- **Frameworks**: Core ML → **Core AI** (announced for WWDC 2026 / iOS 27)
  - Will support LLMs and diffusion models natively
  - Model Context Protocol (MCP) for third-party model integration
  - Foundation Models framework (iOS 26+) lets developers use Apple's on-device model

- **Ferret-UI Lite**: 3B parameter model for autonomous app control (tap, type, navigate)

### Samsung Galaxy AI Architecture (2026)

- **Hardware**: Snapdragon 8 Elite NPU (45+ TOPS), 12 GB RAM
- **On-Device**: Upgraded NPU handles transcription, image segmentation, rewriting locally
- **Multi-Agent Orchestration**: Galaxy S26 uses three AI systems:
  1. **Bixby** — on-device assistant (local)
  2. **Gemini** — autonomous app control (can operate Uber, etc.)
  3. **Perplexity** — web queries (via Sonar API, deeply integrated)
  
- **AI as Orchestrator**: Samsung positions itself as AI router, not model provider
  - Routes requests to best available AI agent
  - "Agentic" architecture — agents plan and execute multi-step tasks autonomously
  
- **On-Device Models**: Gemini Nano (1.8B / 3.25B params) built into Android 14+
  - Accessed via AICore system service
  - Supports text summarization, smart replies, rewriting, proofreading
  - Multimodal on Pixel 9+: image description, speech recognition

- **Developer Access**: 
  - MediaPipe LLM Inference API
  - AICore for Gemini Nano
  - QNN backend for custom models on NPU

---

## 8. Battery Impact Summary

| Model | Battery per 25 Conversations | Impact Level |
|-------|------------------------------|-------------|
| Gemma 3 270M | **0.75%** | Negligible |
| Qwen3 0.6B | ~1-2% (estimated) | Very Low |
| Gemma 3n E2B | ~2-4% (estimated) | Low |
| Gemma 3 1B | ~2-3% (estimated) | Low |
| Llama 3.2 1B | ~2-3% (estimated) | Low |
| Llama 3.2 3B | ~4-6% (estimated) | Moderate |
| Phi-4-mini 3.8B | ~4-8% (estimated) | Moderate |
| Phi-4-mini-flash 3.8B | ~3-5% (40% savings vs standard) | Low-Moderate |

**Key insight**: Load-on-demand with 30-second idle timeout keeps battery impact minimal. The router model (270M) stays loaded permanently with negligible cost.

---

## 9. Can Models Run Alongside Normal Phone Workload?

**Yes, with proper memory management:**

| Model Tier | RAM Used | iPhone 16 Pro Headroom | S25 Ultra Headroom | Verdict |
|------------|----------|----------------------|-------------------|---------|
| Router (270M) | ~125 MB | ~4.9 GB free | ~7.9 GB free | ✅ Always fine |
| Brain (E2B ~2B) | ~1.5 GB | ~3.5 GB free | ~6.5 GB free | ✅ Fine |
| Router + Brain | ~1.6 GB | ~3.4 GB free | ~6.4 GB free | ✅ Fine |
| 3B model | ~2-3 GB | ~2-3 GB free | ~5-6 GB free | ⚠️ Tight on iPhone |
| 3.8B model | ~3 GB | ~2 GB free | ~5 GB free | ⚠️ iPhone risk, S25 OK |

**Strategy**: Keep model in memory only during active use. Unload aggressively. The OS memory pressure system will force-unload if needed — design for graceful handling.

---

## 10. License Comparison

| Model | License | Commercial Use | Fine-tuning | Redistribution |
|-------|---------|---------------|-------------|---------------|
| Gemma 3/3n | Gemma Terms | ✅ | ✅ | ✅ (with terms) |
| Gemma 3 270M | Gemma Terms | ✅ | ✅ | ✅ |
| Qwen3/3.5 0.6-0.8B | Apache 2.0 | ✅ | ✅ | ✅ |
| Qwen 2.5 3B | Apache 2.0 | ✅ | ✅ | ✅ |
| Llama 3.2 1B/3B | Meta Community | ✅ (<700M MAU) | ✅ | ✅ (with terms) |
| MobileLLM-Pro 1B | Meta Community | ✅ (<700M MAU) | ✅ | ✅ |
| Phi-4-mini 3.8B | MIT | ✅ | ✅ | ✅ |
| SmolLM2 1.7B | Apache 2.0 | ✅ | ✅ | ✅ |
| Jamba 3B | Apache 2.0 | ✅ | ✅ | ✅ |

**All viable models have licenses compatible with Jarvis personal use.**

---

## 11. Final Recommendations for Jarvis

### Immediate Implementation (Phase 1)

1. **Router Model**: **Gemma 3 270M** (INT4, 125 MB)
   - Always loaded, classifies intents
   - Routes to: local brain / desktop / cloud / direct action
   - Framework: MediaPipe (Android), Core ML (future iOS)

2. **On-Device Brain**: **Gemma 3n E2B** (INT4, ~1.5 GB)
   - Loaded on-demand for Q&A, summarization, command processing
   - Multimodal: can process images, audio
   - Framework: MediaPipe (Android), llama.cpp fallback

3. **Inference Framework**: **llama.cpp** (cross-platform, GGUF format)
   - Kotlin JNI binding for Android
   - Mature, well-tested, widest model support
   - Fallback: MediaPipe for Gemma-specific NPU acceleration

### Future Enhancement (Phase 2)

4. **LoRA Personalization Pipeline**:
   - Desktop trains LoRA adapters from conversation history
   - Sync LoRA weights (~1-10 MB) to phone via existing encrypted sync
   - Hot-swap adapters in llama.cpp at runtime

5. **Upgrade Path**: When Qwen3.5-0.8B matures, consider as router upgrade (multimodal, 201 languages)

### Architecture Decision Records

- **Why Gemma 3n E2B over Llama 3.2 3B?** Better quality-per-byte due to MatFormer selective activation. 2B memory footprint but 5-8B quality.
- **Why not Phi-4-mini?** At 3.8B, it's tight on iPhone. Gemma 3n E2B delivers comparable quality at lower memory.
- **Why llama.cpp over ExecuTorch?** Cross-platform (Android + future iOS) with single GGUF format. ExecuTorch requires separate export per platform.
- **Why two models (router + brain)?** The 270M router is always-on with negligible battery. Loading the full brain only when needed saves significant battery and RAM.
- **Why not Gemini Nano?** Closed-source, limited to Samsung/Pixel, no custom fine-tuning possible. Jarvis needs full control.

---

## 12. Key Research Sources

1. "On-Device LLMs: State of the Union, 2026" — v-chandra.github.io (comprehensive overview)
2. Google Developers Blog — Gemma 3 on mobile, Gemma 3n developer guide, Gemma 3 270M
3. Unsloth.ai — QAT + ExecuTorch deployment pipeline for mobile
4. MobileLLM-Pro Technical Report — arxiv.org/abs/2511.06719
5. MobileFineTuner — arxiv.org/html/2512.08211v1
6. Apple ML Research — Foundation Language Models, Core AI framework
7. Samsung Newsroom — Galaxy S26 AI architecture
8. Qualcomm Developer Blog — AI Inference Suite, QNN Accelerator
9. MediaPipe LLM Inference API — ai.google.dev/edge
10. ExecuTorch 1.0 — pytorch.org/executorch
