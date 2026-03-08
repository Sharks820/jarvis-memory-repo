# Local LLM Research — March 2026

## Executive Summary

The local LLM landscape in early 2026 is mature and competitive. Open-source models now rival GPT-4-class performance at the 14B–32B parameter range when properly quantized. For Jarvis, the best strategy is a **tiered model stack**: a fast small model for simple tasks, a capable mid-range model for reasoning, and optional large models for complex analysis.

**Top Picks at a Glance:**
| Hardware Tier | Best Overall Model | Ollama Name | Why |
|---|---|---|---|
| Tier 1 (8GB RAM, CPU) | Qwen 3 4B / Phi-4-mini 3.8B | `qwen3:4b` / `phi4-mini` | Best reasoning per byte |
| Tier 2 (16GB RAM, 6GB VRAM) | Qwen 3 8B (Q4_K_M) | `qwen3:8b` | Best all-rounder at this tier |
| Tier 3 (32GB RAM, 12GB VRAM) | Qwen 3 14B or Phi-4 14B (Q4_K_M) | `qwen3:14b` / `phi4:14b` | Near-GPT-4 reasoning |

---

## 1. Model-by-Model Analysis

### 1.1 Qwen 3 / Qwen 3.5 Series (Alibaba) ⭐ TOP PICK

**Why it matters:** The Qwen family is currently the most versatile open-source model series, consistently topping local LLM leaderboards across reasoning, coding, and multilingual tasks.

| Variant | Params | Q4_K_M Size | VRAM (Q4) | RAM (CPU) | MMLU | Speed (RTX 3060) | Ollama |
|---|---|---|---|---|---|---|---|
| Qwen3 0.6B | 0.6B | ~400MB | <1GB | 2GB | ~45% | 100+ tok/s | `qwen3:0.6b` |
| Qwen3 4B | 4B | ~2.5GB | 3-4GB | 6GB | ~62% | 60+ tok/s | `qwen3:4b` |
| Qwen3 8B | 8B | ~5GB | 6-8GB | 10GB | ~72% | 40+ tok/s | `qwen3:8b` |
| Qwen3 14B | 14B | ~8.5GB | 10-12GB | 16GB | ~78% | 25-35 tok/s | `qwen3:14b` |
| Qwen3 32B | 32B | ~19GB | 20-24GB | 36GB | ~83% | 12-18 tok/s | `qwen3:32b` |
| Qwen3 30B-A3B (MoE) | 30B (3B active) | ~18GB | 10-12GB | 20GB | ~75% | 50-73 tok/s | `qwen3:30b-a3b` |
| Qwen 2.5 Coder 7B | 7B | ~4.5GB | 5-6GB | 8GB | N/A (code) | 40+ tok/s | `qwen2.5-coder:7b` |

**Qwen 3.5 (Feb 2026 release):**
- Flagship: 397B-A17B (MoE) — too large for consumer hardware
- 35B-A3B variant: ~22GB VRAM at Q4_K_M, 44 tok/s on RTX 5060 Ti (16GB)
- Uses Gated Delta Networks architecture for faster decoding
- Context: up to 262K tokens (extendable to 1M)

**Strengths:** Best-in-class reasoning, coding, multilingual (119+ languages), dual-mode (thinking/non-thinking), Apache 2.0 license
**Weaknesses:** Larger variants need significant VRAM; some models aggressive on thinking tokens
**License:** Apache 2.0 (fully free for private use) ✅

---

### 1.2 Phi-4 Family (Microsoft) ⭐ REASONING CHAMPION

| Variant | Params | Q4_K_M Size | VRAM (Q4) | RAM (CPU) | MMLU | HumanEval | Speed | Ollama |
|---|---|---|---|---|---|---|---|---|
| Phi-4-mini | 3.8B | ~2.3GB | 3-4GB | 6GB | ~72% | ~75% | 50-80 tok/s | `phi4-mini` |
| Phi-4 | 14B | ~8.5GB | 10-12GB | 16GB | 84.8% | 82.6% | 25-40 tok/s | `phi4` |
| Phi-4-reasoning | 14B | ~8.5GB | 10-12GB | 16GB | ~82% | 93%+ | 20-35 tok/s | `phi4-reasoning` |

**Strengths:** Exceptional STEM/math reasoning, surpasses GPT-4o on GPQA and MATH benchmarks, small footprint for its performance, MIT license
**Weaknesses:** 16K context window (smaller than competitors), less strong on creative/conversational tasks, English-centric
**License:** MIT (fully free for private use) ✅

---

### 1.3 Gemma 3 Family (Google DeepMind)

| Variant | Params | Q4_K_M Size | VRAM (Q4) | RAM (CPU) | MMLU | Speed | Ollama |
|---|---|---|---|---|---|---|---|
| Gemma 3 1B | 1B | ~700MB | <1GB | 2GB | ~40% | 100+ tok/s | `gemma3:1b` |
| Gemma 3 4B | 4B | ~2.5GB | 3-4GB | 6GB | ~60% | 40-60 tok/s | `gemma3:4b` |
| Gemma 3 12B | 12B | ~7.5GB | 10-12GB | 16GB | ~72% | 25-33 tok/s | `gemma3:12b` |
| Gemma 3 27B | 27B | ~16GB | 20-24GB | 32GB | 78.6% | 12-18 tok/s | `gemma3:27b` |

**Gemma 3n (2026 preview):** Mobile-first, ~2-3GB RAM, selective parameter activation, 1.5x faster than Gemma 3

**Strengths:** Multimodal (vision + text on 4B+), 128K context, 140+ languages, excellent instruction following, permissive license
**Weaknesses:** Higher VRAM usage than expected (KV cache issues at long contexts), some RAM leak issues reported on Windows with Ollama, not the strongest at reasoning
**License:** Gemma Terms of Use (free for most uses, some restrictions on large-scale commercial) ✅ for private use

---

### 1.4 Llama 3.x / Llama 4 Scout (Meta)

| Variant | Params | Q4_K_M Size | VRAM (Q4) | RAM (CPU) | MMLU | Speed | Ollama |
|---|---|---|---|---|---|---|---|
| Llama 3.2 3B | 3B | ~2GB | 3-4GB | 6GB | ~63% | 40-60 tok/s | `llama3.2:3b` |
| Llama 3.1 8B | 8B | ~5GB | 6-8GB | 14GB | ~73% | 40+ tok/s | `llama3.1:8b` |
| Llama 3.3 70B | 70B | ~40GB | 42-48GB | 80GB+ | ~86% | 5-8 tok/s | `llama3.3:70b` |
| Llama 4 Scout | 109B (17B active) | ~65GB | 48GB+ | 80GB+ | ~82%* | ~45 tok/s (H100) | `llama4:scout` |

**Llama 4 Scout details:**
- MoE: 16 experts, 2 active per token (17B active of 109B total)
- 10M token context window (largest open model)
- Multimodal (text + images via MetaCLIP)
- Designed for H100 GPU (INT4) — too large for consumer hardware
- Average benchmark: 67.3% across 12 tests (mixed results)

**Strengths:** Huge ecosystem, battle-tested, excellent instruction following, 128K context (3.x), massive context (Scout)
**Weaknesses:** Llama 4 Scout too large for consumer hardware; Llama 3.x being superseded by Qwen/Phi in benchmarks per parameter
**License:** Llama Community License (free up to 700M MAU) ✅ for private use

---

### 1.5 Mistral Small 3.1 / Mistral Nemo (Mistral AI)

| Variant | Params | Q4_K_M Size | VRAM (Q4) | RAM (CPU) | MMLU | HumanEval | Speed | Ollama |
|---|---|---|---|---|---|---|---|---|
| Mistral 7B v0.3 | 7B | ~4.4GB | 5-6GB | 8GB | ~63% | ~65% | 40+ tok/s | `mistral:7b` |
| Mistral Nemo 12B | 12B | ~7.5GB | 10-12GB | 16GB | ~68% | ~72% | 25-35 tok/s | `mistral-nemo:12b` |
| Mistral Small 3.1 | 24B | ~14GB | 16-18GB | 28GB | ~66% | 85.9% | ~150 tok/s* | `mistral-small3.1` |

*150 tok/s is Mistral's claim on high-end hardware (RTX 4090 / 32GB Mac)

**Strengths:** Excellent function calling, 128K context (3.1), multimodal, fast inference, strong coding, Apache 2.0
**Weaknesses:** MMLU scores lower than Qwen/Phi at same size; 24B needs more VRAM than 14B alternatives
**License:** Apache 2.0 ✅

---

### 1.6 DeepSeek R1 / V3 (DeepSeek)

| Variant | Params | Q4_K_M Size | VRAM (Q4) | RAM (CPU) | Performance | Speed | Ollama |
|---|---|---|---|---|---|---|---|
| DS-R1-Distill-Qwen-7B | 7B | ~4.5GB | 5-6GB | 8GB | MMLU-Pro: 49.1 | 35-45 tok/s | `deepseek-r1:7b` |
| DS-R1-Distill-Qwen-14B | 14B | ~8.5GB | 10-12GB | 16GB | MMLU-Pro: 59.1 | 20-30 tok/s | `deepseek-r1:14b` |
| DS-R1-Distill-Qwen-32B | 32B | ~19GB | 20-24GB | 36GB | ~o1-mini level | 10-15 tok/s | `deepseek-r1:32b` |
| DeepSeek V3.1 (full) | 671B | ~245GB (2bit) | 100GB+ | 300GB+ | SOTA | N/A consumer | `deepseek-v3` |

**Strengths:** Exceptional chain-of-thought reasoning (visible thinking), competitive with GPT-4 on reasoning, MIT license, distilled versions very accessible
**Weaknesses:** Verbose (long thinking chains = slow effective speed), distilled versions lose some capability, full model impractical for consumer hardware
**License:** MIT ✅

---

### 1.7 Other Notable Models

**Falcon H1R 7B (TII):**
- 7B params, exceptional reasoning (88.1% AIME-24)
- Uses reinforcement learning + GRPO for reasoning quality
- Available on HuggingFace, not yet mainstream on Ollama

**Qwen3-Coder-Next (Alibaba, Feb 2026):**
- 80B total, 3B active (MoE), 256K context
- 70.6% SWE-Bench (rivals Claude Opus 4.5 at 87%)
- Runs on ~8GB VRAM with CPU offload
- Ollama: `qwen3-coder-next`

**GLM-4.7 / GLM-5 (Zhipu AI):**
- GLM-5 topped open-source leaderboard (Quality Index 49.64)
- GLM-4.7 355B: HumanEval 94.2%
- Very large, not practical for consumer tiers

**SmolLM3 3B (HuggingFace):**
- Fully open model with transparent training
- Outperforms Llama 3.2 3B on several benchmarks
- Good for classification and lightweight tasks

---

## 2. Quantization Guide

### Quantization Formats (GGUF — the standard for Ollama)

| Format | Bits | Quality Loss | Size vs FP16 | Best For |
|---|---|---|---|---|
| Q8_0 | 8-bit | Near zero | ~50% | Maximum quality, enough VRAM |
| Q6_K | 6-bit | Minimal | ~40% | High quality, moderate VRAM |
| Q5_K_M | 5-bit | Very small | ~35% | Sweet spot for coding tasks |
| Q4_K_M | 4-bit | Small | ~28% | **Best balance** quality/size |
| Q4_K_S | 4-bit | Small-moderate | ~26% | Slightly smaller, minor quality hit |
| Q3_K_M | 3-bit | Moderate | ~22% | When VRAM is very tight |
| Q2_K | 2-bit | Significant | ~15% | Emergency/testing only |
| IQ4_NL | ~4-bit | Small | ~27% | Advanced imatrix, good quality |

**Recommendation:** **Q4_K_M** is the gold standard for local use. It provides the best tradeoff between quality retention and memory savings. Use Q5_K_M for coding tasks where precision matters.

### Speed Optimization Techniques

1. **Speculative Decoding (2-3x speedup):**
   - Uses a small "draft" model to predict tokens, verified by the main model
   - Best in predictable domains (code, templates): 75%+ acceptance rate → 3x speedup
   - SpecDec++ and Saguaro algorithms (2026) achieve up to 5x over autoregressive
   - Ollama has partial support; llama.cpp has full support

2. **Multi-Token Prediction:**
   - Predicts multiple tokens simultaneously
   - Combined with speculative decoding: 160+ tok/s on 70B models (RTX 3090)

3. **Flash Attention / Paged Attention:**
   - Reduces VRAM usage for long contexts
   - Enabled by default in modern Ollama versions

4. **KV Cache Quantization:**
   - Quantize the attention cache to Q8 or Q4 to reduce VRAM
   - Especially important for long contexts (8K+ tokens)
   - Set via `OLLAMA_KV_CACHE_TYPE=q8_0` environment variable

---

## 3. Hardware Tier Recommendations

### Tier 1: Minimum (8GB RAM, CPU Only)

**Constraints:** No GPU, limited RAM, need fast simple responses

| Rank | Model | Ollama Command | RAM Used | Speed (CPU) | Best For |
|---|---|---|---|---|---|
| 🥇 | Qwen3 4B Q4_K_M | `ollama run qwen3:4b` | ~4GB | 8-15 tok/s | All-round assistant |
| 🥈 | Phi-4-mini Q4_K_M | `ollama run phi4-mini` | ~4GB | 8-15 tok/s | Reasoning/STEM |
| 🥉 | Gemma 3 4B Q4_K_M | `ollama run gemma3:4b` | ~4GB | 8-12 tok/s | Multimodal (images) |
| 4th | Llama 3.2 3B Q4_K_M | `ollama run llama3.2:3b` | ~3GB | 10-18 tok/s | Fast simple tasks |
| 5th | Qwen3 0.6B | `ollama run qwen3:0.6b` | ~1GB | 30+ tok/s | Classification only |

**Strategy for Jarvis:** Use **Qwen3 4B** as the primary local model. It handles privacy queries, offline operation, and simple commands well. Use **Qwen3 0.6B** for ultra-fast classification/routing tasks.

---

### Tier 2: Recommended (16GB RAM, GTX 1660 / 6GB VRAM)

**Constraints:** 6GB VRAM limits model to ~5GB after OS overhead

| Rank | Model | Ollama Command | VRAM | RAM Spill | Speed | Best For |
|---|---|---|---|---|---|---|
| 🥇 | Qwen3 8B Q4_K_M | `ollama run qwen3:8b` | ~5GB | 1-2GB | 30-40 tok/s | **Best all-rounder** |
| 🥈 | Llama 3.1 8B Q4_K_M | `ollama run llama3.1:8b` | ~5GB | 1-2GB | 30-40 tok/s | Instruction following |
| 🥉 | Phi-4-mini Q5_K_M | `ollama run phi4-mini:q5_K_M` | ~3GB | minimal | 40-60 tok/s | Fast reasoning |
| 4th | Gemma 3 4B Q5_K_M | `ollama run gemma3:4b` | ~3GB | minimal | 40-50 tok/s | Multimodal tasks |
| 5th | DS-R1-Distill-7B Q4_K_M | `ollama run deepseek-r1:7b` | ~5GB | 1-2GB | 25-35 tok/s | Deep reasoning (slow) |

**Strategy for Jarvis:** Primary: **Qwen3 8B**. Secondary: **Phi-4-mini** for fast classification/routing. Keep **DS-R1-Distill-7B** for when deep reasoning is needed offline.

**Multi-model approach (fits in 16GB RAM):**
- Router/classifier: Qwen3 0.6B (~1GB)
- Fast responder: Phi-4-mini Q4 (~4GB)  
- Quality responder: Qwen3 8B Q4 (~5GB, loaded on demand)

---

### Tier 3: Optimal (32GB RAM, RTX 3060 / 12GB VRAM)

**Constraints:** 12GB VRAM allows 14B models fully in VRAM or 32B partially

| Rank | Model | Ollama Command | VRAM | Speed | Best For |
|---|---|---|---|---|---|
| 🥇 | Qwen3 14B Q4_K_M | `ollama run qwen3:14b` | ~9GB | 25-35 tok/s | **Best quality/speed** |
| 🥈 | Phi-4 14B Q4_K_M | `ollama run phi4` | ~9GB | 25-40 tok/s | STEM reasoning champion |
| 🥉 | DS-R1-Distill-14B Q4_K_M | `ollama run deepseek-r1:14b` | ~9GB | 20-30 tok/s | Chain-of-thought reasoning |
| 4th | Gemma 3 12B Q4_K_M | `ollama run gemma3:12b` | ~8GB | 25-33 tok/s | Multimodal + 128K context |
| 5th | Qwen3 30B-A3B (MoE) Q4 | `ollama run qwen3:30b-a3b` | ~12GB | 50-73 tok/s | Fast + high quality (MoE) |
| 6th | Mistral Small 3.1 Q4_K_M | `ollama run mistral-small3.1` | ~14GB* | 20-30 tok/s | Function calling |

*Mistral Small 3.1 at 24B needs partial CPU offload on 12GB VRAM

**Strategy for Jarvis:** Primary: **Qwen3 14B** for quality. Secondary: **Phi-4** for STEM tasks. Keep **Qwen3 8B** loaded for fast responses. Use **DS-R1-Distill-14B** for complex reasoning.

**Multi-model approach (fits in 32GB RAM + 12GB VRAM):**
- Router: Qwen3 0.6B (always in RAM, ~1GB)
- Fast: Qwen3 8B Q4 (in VRAM when active, ~5GB)
- Quality: Qwen3 14B Q4 (in VRAM when active, ~9GB)
- Reasoning: DS-R1-Distill-14B Q4 (swap in for complex tasks, ~9GB)

---

## 4. Jarvis-Specific Recommendations

### Recommended Model Stack for Jarvis

Given Jarvis's architecture (Ollama local + cloud fallback), here's the optimal configuration:

```
LOCAL MODELS (via Ollama):
├── Router/Classifier: qwen3:0.6b          # Always loaded, <1GB, instant classification
├── Fast Responder:    phi4-mini            # Simple commands, 3-4GB, very fast
├── Quality Local:     qwen3:8b or qwen3:14b  # Privacy queries, offline, 5-9GB
├── Deep Reasoning:    deepseek-r1:14b      # Complex offline reasoning, 9GB
└── Code Tasks:        qwen2.5-coder:7b     # Code generation/review, 5GB

CLOUD FALLBACK (existing):
├── Claude (Anthropic)  # Complex reasoning, long context
├── Gemini (Google)     # Multimodal, large context
└── Groq               # Fast inference for non-private queries
```

### Model Selection by Task Type

| Task | Model | Why |
|---|---|---|
| Route/classify query | qwen3:0.6b | Ultra-fast, minimal resources |
| Simple commands ("set timer", "what time is it") | phi4-mini | Fast, accurate, small |
| Privacy-sensitive queries | qwen3:8b/14b | Good quality, fully offline |
| Learning/embedding classification | qwen3:4b | Good balance for ML tasks |
| Offline complex reasoning | deepseek-r1:14b | Chain-of-thought visible reasoning |
| Code generation/review | qwen2.5-coder:7b | Purpose-built for code |
| Conversation/chat | qwen3:8b/14b | Natural, multilingual |
| Knowledge graph extraction | phi4 | Structured output, reasoning |

### Ollama Configuration Tips

```bash
# Set environment for optimal performance
set OLLAMA_NUM_PARALLEL=2          # Allow 2 concurrent requests
set OLLAMA_MAX_LOADED_MODELS=3     # Keep 3 models in memory
set OLLAMA_KV_CACHE_TYPE=q8_0      # Quantize KV cache for less VRAM
set OLLAMA_FLASH_ATTENTION=1       # Enable flash attention

# Pre-pull recommended models
ollama pull qwen3:0.6b
ollama pull phi4-mini
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull deepseek-r1:14b
ollama pull qwen2.5-coder:7b
```

---

## 5. Benchmark Comparison Table (Normalized)

| Model | Params | MMLU | HumanEval | Reasoning* | Coding* | Speed (12GB GPU) | License |
|---|---|---|---|---|---|---|---|
| Qwen3 0.6B | 0.6B | ~45% | ~35% | ⭐⭐ | ⭐⭐ | 100+ tok/s | Apache 2.0 |
| Llama 3.2 3B | 3B | 63.4% | ~55% | ⭐⭐⭐ | ⭐⭐⭐ | 50-60 tok/s | Llama |
| Phi-4-mini 3.8B | 3.8B | ~72% | ~75% | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 50-80 tok/s | MIT |
| Gemma 3 4B | 4B | ~60% | ~55% | ⭐⭐⭐ | ⭐⭐⭐ | 40-60 tok/s | Gemma ToU |
| Qwen3 4B | 4B | ~62% | ~60% | ⭐⭐⭐⭐ | ⭐⭐⭐ | 60+ tok/s | Apache 2.0 |
| Mistral 7B v0.3 | 7B | ~63% | ~65% | ⭐⭐⭐ | ⭐⭐⭐ | 40+ tok/s | Apache 2.0 |
| DS-R1-Distill 7B | 7B | ~65%* | ~60% | ⭐⭐⭐⭐ | ⭐⭐⭐ | 35-45 tok/s | MIT |
| Qwen3 8B | 8B | ~72% | ~70% | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 40+ tok/s | Apache 2.0 |
| Llama 3.1 8B | 8B | ~73% | ~68% | ⭐⭐⭐⭐ | ⭐⭐⭐ | 40+ tok/s | Llama |
| Gemma 3 12B | 12B | ~72% | ~68% | ⭐⭐⭐⭐ | ⭐⭐⭐ | 25-33 tok/s | Gemma ToU |
| Mistral Nemo 12B | 12B | ~68% | ~72% | ⭐⭐⭐ | ⭐⭐⭐⭐ | 25-35 tok/s | Apache 2.0 |
| **Phi-4 14B** | **14B** | **84.8%** | **82.6%** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | **25-40 tok/s** | **MIT** |
| **Qwen3 14B** | **14B** | **~78%** | **~75%** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | **25-35 tok/s** | **Apache 2.0** |
| DS-R1-Distill 14B | 14B | ~72%* | ~70% | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 20-30 tok/s | MIT |
| Mistral Small 3.1 | 24B | ~66% | 85.9% | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 20-30 tok/s | Apache 2.0 |
| Gemma 3 27B | 27B | 78.6% | ~72% | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 12-18 tok/s | Gemma ToU |
| Qwen3 32B | 32B | ~83% | ~80% | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 12-18 tok/s | Apache 2.0 |
| DS-R1-Distill 32B | 32B | ~78%* | ~75% | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 10-15 tok/s | MIT |

*Reasoning scores are relative ratings; DS-R1 uses MMLU-Pro (different scale, ~49-59%)
*Speed assumes Q4_K_M quantization on RTX 3060 12GB

---

## 6. Key Findings & Trends (March 2026)

1. **Qwen 3 dominates the local LLM space.** Best quality-per-parameter at every size tier, with Apache 2.0 licensing. The dual-mode (thinking/non-thinking) is particularly useful for an assistant like Jarvis.

2. **Phi-4 14B punches way above its weight.** 84.8% MMLU at just 14B params is remarkable. Best choice for STEM-heavy reasoning tasks. MIT license is ideal.

3. **MoE models are the future for local inference.** Qwen3 30B-A3B (only 3B active params) runs at 50-73 tok/s while providing 30B-level quality. Qwen3-Coder-Next (80B total, 3B active) achieves 70.6% SWE-Bench.

4. **DeepSeek R1 distilled models are the best free reasoning models.** Visible chain-of-thought reasoning is unique and powerful. The 14B distill approaches o1-mini performance.

5. **Gemma 3 is the multimodal champion for local use.** Best choice if Jarvis needs to process images locally. 128K context is generous.

6. **Llama 4 Scout is too large for consumer hardware.** Despite being MoE (17B active), the full 109B model doesn't fit consumer GPUs. Llama 3.1/3.2/3.3 remain the practical choices.

7. **Speculative decoding can double inference speed** with no quality loss, but requires software support and tuning.

8. **Q4_K_M quantization** is the standard recommendation — minimal quality loss with ~72% memory savings vs FP16.

9. **The gap between local and cloud models is closing fast.** Qwen3 32B Q4 on a 24GB GPU now approaches Claude Sonnet-level performance for many tasks.

10. **All top models are available on Ollama.** The ecosystem is mature and well-supported.

---

## 7. Sources & Research Trail

- BentoML: "Best Open-Source LLMs in 2026" — model overview and Qwen3.5 details
- LocalAIMaster: "Small Language Models Guide 2026" — Phi-4, Gemma 3, Qwen 3 specs
- InsiderLLM: "Best Local LLMs for Mac 2026" — hardware tier recommendations
- InsiderLLM: "Phi Models Guide" / "Qwen 3.5 Local Guide" — detailed benchmarks
- Ollama library pages: mistral-small, mistral-small3.1, qwen2.5, etc.
- WhatLLM.org: "Best Open Source LLM Feb 2026" — GLM-5, Kimi K2.5 rankings
- LocalLLM.in: "Ollama VRAM Requirements 2026" — comprehensive VRAM guide
- AIRank.dev: Phi-4, Gemma 3, Llama 4 Scout benchmark reviews
- Reddit r/LocalLLaMA: Qwen 3 performance benchmarks across hardware
- Medium: Speculative decoding speed improvements (Amar Chetri)
- Clarifai: "Top 10 Open-source Reasoning Models 2026"
- Meta AI Blog: Llama 4 multimodal intelligence
- Mistral AI: Mistral Small 3.1 announcement
- Various Ollama GitHub issues: VRAM/RAM requirements real-world data
- Hypereal.tech: "Best Small Local LLMs 2026" / Qwen3 quantization guide
- PreMAI Blog: "15 Best Lightweight Language Models 2026"
- Marc0.dev: Qwen3-Coder-Next review (70% SWE-Bench)
- OpenReview: Speculative Speculative Decoding (SSD) paper
