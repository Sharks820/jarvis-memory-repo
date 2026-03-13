# Jarvis Self-Learning Brain: Design & Requirements

**Date**: 2026-03-13
**Author**: Claude Opus 4.6 + Conner McCarthy
**Goal**: Jarvis becomes its own LLM, learning from data ingested across all LLM interactions

---

## Executive Summary

Transform Jarvis from an LLM consumer into an LLM producer — a personal AI that accumulates knowledge from every interaction with Claude, Gemini, Codex, and Qwen, then serves its own responses without external LLM dependency. This is a hybrid approach combining fine-tuning, RAG, and continual learning.

---

## 1. Architecture: The Jarvis Brain Stack

```
Layer 4: PERSONALITY ENGINE
  - Response style conditioning (Jarvis voice, tone, personality)
  - Owner-specific behavior patterns learned from interaction history
  - Contextual awareness (time, location, activity, mood)

Layer 3: KNOWLEDGE SYNTHESIZER
  - Cross-LLM knowledge distillation pipeline
  - Contradiction resolution across LLM sources
  - Fact confidence scoring with provenance tracking
  - Automatic curriculum generation from knowledge gaps

Layer 2: PERSONAL MODEL (Fine-tuned)
  - Base: Qwen3.5 4B or Phi-3 Mini 3.8B (fits 8GB VRAM for training)
  - QLoRA fine-tuned on Jarvis conversation corpus
  - Continually updated with new interactions (weekly training cycles)
  - Specialized for Conner's domain: software engineering, AI, personal assistant tasks

Layer 1: RETRIEVAL FOUNDATION
  - Enhanced RAG over knowledge graph + memory system
  - sqlite-vec embeddings for semantic retrieval
  - Dynamic few-shot example selection from conversation history
  - Fact injection from knowledge graph with confidence weights
```

---

## 2. Data Pipeline: Learning From Every LLM

### 2.1 Capture Layer (Already Built)
Jarvis already captures LLM interactions through:
- `conversation_state.py` — Tracks conversations across all providers
- `learning/consolidator.py` — Clusters episodic memories into semantic facts
- `knowledge/graph.py` — Stores verified facts with provenance
- `gateway/models.py` — Routes to and captures responses from all LLMs

### 2.2 Training Data Generation (New)
```
Raw LLM Responses → Quality Filter → Format Converter → Training Dataset

Steps:
1. Extract (prompt, response) pairs from conversation history
2. Score quality: Was the response helpful? (user feedback, follow-up patterns)
3. Tag domain: coding, personal, knowledge, reasoning, creative
4. Convert to instruction-tuning format (alpaca/sharegpt)
5. Deduplicate and balance across domains
6. Version-stamp for continual learning checkpoints
```

### 2.3 Knowledge Distillation (New)
```
Cloud LLM (Teacher) → Distillation Pipeline → Local Model (Student)

Process:
1. Send complex prompts to Claude/Gemini (teacher models)
2. Capture high-quality reasoning chains
3. Generate training pairs that teach the local model to reason similarly
4. Focus on domains where local model underperforms
5. Progressive difficulty: start with simple Q&A, advance to multi-step reasoning
```

---

## 3. Hardware Requirements & Feasibility

### Current System
- **GPU**: RTX 4060 Ti 8GB VRAM
- **RAM**: 32GB
- **CPU**: Ryzen 7 5700
- **Storage**: Assume 500GB+ available

### Training Feasibility (QLoRA)

| Model | VRAM (Training) | VRAM (Inference) | Feasible? |
|-------|-----------------|-------------------|-----------|
| Qwen3.5 4B | ~6GB QLoRA | ~3.4GB Q4 | YES |
| Phi-3 Mini 3.8B | ~5.5GB QLoRA | ~2.5GB Q4 | YES |
| Qwen3.5 9B | ~12GB QLoRA | ~6.6GB Q4 | NO (train), YES (infer) |
| Llama 3.2 3B | ~5GB QLoRA | ~2GB Q4 | YES |
| SmolLM2 1.7B | ~3GB QLoRA | ~1.2GB Q4 | YES (fast iteration) |

**Recommended base model**: **Qwen3.5 4B** — same family as current local model, proven quality, fits for both training and inference.

### Training Time Estimates
- 1,000 examples: ~15 minutes (QLoRA, 3 epochs)
- 10,000 examples: ~2.5 hours
- 50,000 examples: ~12 hours
- 100,000 examples: ~24 hours

### Storage Requirements
- Training dataset: ~50MB per 10K examples
- Model checkpoints: ~4GB each (keep 5 = 20GB)
- LoRA adapters: ~100MB each (keep 20 = 2GB)
- Knowledge base: Already managed by existing SQLite system

---

## 4. Implementation Phases

### Phase 1: Data Collection & Formatting (1-2 weeks)
**Goal**: Build the training data pipeline

1. Create `engine/src/jarvis_engine/brain/` subpackage
2. `brain/data_collector.py` — Extract (prompt, response) pairs from:
   - Conversation history in memory DB
   - Gateway audit logs (already captured)
   - Knowledge graph facts (as Q&A pairs)
   - Learning mission results
3. `brain/data_formatter.py` — Convert to training format:
   - Alpaca format: `{"instruction": ..., "input": ..., "output": ...}`
   - Quality scoring: discard low-quality/error responses
   - Domain tagging for balanced curriculum
4. `brain/data_validator.py` — Validate training data:
   - No PII in training data (privacy filter)
   - No contradictory facts
   - Minimum quality threshold
5. Target: 5,000+ high-quality training examples from existing data

### Phase 2: Initial Fine-Tuning (1 week)
**Goal**: First Jarvis brain checkpoint

1. Install training stack:
   - `unsloth` (2-4x faster QLoRA training)
   - `transformers`, `peft`, `bitsandbytes`
   - `trl` (for RLHF later)
2. `brain/trainer.py` — QLoRA training pipeline:
   - Load Qwen3.5 4B in 4-bit quantization
   - Apply LoRA adapters (rank=16, alpha=32)
   - Train on collected dataset
   - Save LoRA adapter checkpoints
3. `brain/evaluator.py` — Quality evaluation:
   - Test set holdout (10% of data)
   - Compare against base model
   - Domain-specific benchmarks
   - Jarvis personality consistency check
4. Integration: Load LoRA adapter in Ollama via modelfile

### Phase 3: RAG Enhancement (1 week)
**Goal**: Knowledge-grounded responses

1. `brain/rag_engine.py` — Enhanced retrieval:
   - Hybrid search: BM25 (FTS5) + semantic (sqlite-vec)
   - Knowledge graph fact injection with confidence scores
   - Conversation history context (5-turn sliding window)
   - Dynamic few-shot example selection
2. `brain/response_builder.py` — Prompt construction:
   - System prompt with Jarvis personality
   - Retrieved facts as grounding context
   - Few-shot examples from similar past interactions
   - Owner preference conditioning
3. Quality comparison: RAG-enhanced vs base fine-tuned

### Phase 4: Continual Learning Loop (2 weeks)
**Goal**: Jarvis gets smarter every week

1. `brain/continual_trainer.py` — Automated retraining:
   - Weekly training cycle (configurable)
   - Incremental data: only new interactions since last training
   - Catastrophic forgetting prevention (replay buffer of 10% old data)
   - Checkpoint management (keep best 5 adapters)
2. `brain/curriculum.py` — Smart training prioritization:
   - Identify knowledge gaps (failed queries, low-confidence responses)
   - Generate targeted distillation from cloud LLMs
   - Balance: 40% new interactions, 30% gap-filling, 30% replay
3. `brain/quality_gate.py` — Prevent regression:
   - Run evaluation suite before deploying new adapter
   - A/B test: new adapter vs current on held-out prompts
   - Rollback if quality drops >5%
4. Daemon integration: Add training cycle to daemon_loop.py

### Phase 5: Knowledge Distillation (2 weeks)
**Goal**: Learn reasoning from cloud models

1. `brain/distiller.py` — Teacher-student pipeline:
   - Select challenging prompts where local model struggles
   - Send to Claude/Gemini with chain-of-thought prompting
   - Capture detailed reasoning traces
   - Format as training data with reasoning steps
2. `brain/reasoning_coach.py` — Progressive difficulty:
   - Start: Simple factual Q&A
   - Middle: Multi-step reasoning with explanation
   - Advanced: Complex analysis and synthesis
3. Cost management: Budget-aware distillation scheduling

### Phase 6: Personality & Autonomy (1-2 weeks)
**Goal**: Jarvis feels like Jarvis

1. `brain/personality.py` — Response style conditioning:
   - Extract Conner's preferred response patterns
   - Communication style: direct, technical, no-nonsense
   - Humor calibration, formality level
   - Context-sensitive tone (work vs personal)
2. `brain/autonomy.py` — Self-directed learning:
   - Identify topics Conner frequently asks about
   - Proactively research and pre-learn
   - Build expertise in owner's domains
3. Integration with existing proactive engine

---

## 5. Dependencies & Tools

### Python Packages (New)
```
unsloth>=2024.12          # Fast QLoRA training (2-4x speedup)
peft>=0.13                # LoRA adapter management
bitsandbytes>=0.44        # 4-bit quantization
trl>=0.12                 # Reinforcement learning from human feedback
datasets>=3.0             # HuggingFace dataset utilities
accelerate>=1.0           # Training acceleration
wandb>=0.18               # Experiment tracking (optional)
```

### System Requirements
```
CUDA 12.1+                # Already available with RTX 4060 Ti
cuDNN 9+                  # For training acceleration
~20GB disk                # For model checkpoints
Ollama custom modelfile   # To serve fine-tuned model
```

### External Services (Minimal)
- Cloud LLMs for distillation (already integrated via gateway)
- No new cloud dependencies

---

## 6. Success Criteria

### Milestone 1: "Baby Brain" (Phase 1-2)
- [ ] 5,000+ training examples collected from existing data
- [ ] First LoRA adapter trained on Jarvis corpus
- [ ] Model produces coherent responses in Conner's domain
- [ ] Response quality within 80% of cloud models on basic queries

### Milestone 2: "Informed Brain" (Phase 3)
- [ ] RAG-enhanced responses grounded in knowledge graph
- [ ] Factual accuracy >90% on known facts
- [ ] Conversation context maintained across 5+ turns

### Milestone 3: "Learning Brain" (Phase 4-5)
- [ ] Weekly training cycle running autonomously
- [ ] Quality improves measurably each week
- [ ] Distillation produces reasoning improvements
- [ ] No catastrophic forgetting (regression tests pass)

### Milestone 4: "Jarvis Brain" (Phase 6)
- [ ] Responses feel like Jarvis (personality consistency)
- [ ] Handles 80% of daily queries without cloud LLM fallback
- [ ] Proactively learns about owner's interests
- [ ] Cloud LLMs used only for novel/complex queries

---

## 7. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| VRAM too small for training | Use SmolLM2 1.7B as fallback base model |
| Training data too small | Augment with synthetic data from cloud LLMs |
| Catastrophic forgetting | Replay buffer + quality gate before deployment |
| Quality regression | A/B testing + automatic rollback |
| Privacy leak in training | PII filter on all training data |
| Training instability | Conservative learning rate, gradient checkpointing |
| Disk space exhaustion | Prune old checkpoints, keep best 5 only |

---

## 8. Cost Estimate

| Item | Cost |
|------|------|
| Cloud LLM distillation (Phase 5) | ~$50-100/month (existing APIs) |
| Electricity (weekly training) | ~$5/month |
| Storage | Existing hardware |
| New software | All open-source ($0) |
| **Total monthly** | **~$55-105/month** |

---

## 9. Timeline

| Phase | Duration | Dependency |
|-------|----------|------------|
| Phase 1: Data Pipeline | 1-2 weeks | None |
| Phase 2: Initial Training | 1 week | Phase 1 |
| Phase 3: RAG Enhancement | 1 week | Phase 2 |
| Phase 4: Continual Learning | 2 weeks | Phase 3 |
| Phase 5: Distillation | 2 weeks | Phase 4 |
| Phase 6: Personality | 1-2 weeks | Phase 5 |
| **Total** | **8-10 weeks** | |

---

## 10. Architecture Diagram

```
                    +------------------+
                    |   Conner (User)  |
                    +--------+---------+
                             |
                    +--------v---------+
                    |  Jarvis Engine   |
                    |  (Command Bus)   |
                    +--------+---------+
                             |
              +--------------+---------------+
              |                              |
    +---------v----------+       +-----------v----------+
    |  Jarvis Brain      |       |  Cloud LLM Fallback  |
    |  (Local Model)     |       |  (Claude/Gemini/etc)  |
    |                    |       |                      |
    | +----------------+ |       | Used only for:       |
    | | Fine-tuned     | |       | - Novel queries      |
    | | Qwen3.5 4B     | |       | - Distillation       |
    | | + LoRA Adapter  | |       | - Quality comparison |
    | +----------------+ |       +----------+-----------+
    |                    |                  |
    | +----------------+ |       +----------v-----------+
    | | RAG Engine     | |       |  Distillation        |
    | | (KG + Memory)  | |       |  Pipeline            |
    | +----------------+ |       +----------+-----------+
    |                    |                  |
    | +----------------+ |       +----------v-----------+
    | | Personality    | |       |  Training Data       |
    | | Engine         | |       |  Collector           |
    | +----------------+ |       +----------------------+
    +--------+-----------+
             |
    +--------v-----------+
    | Continual Learning |
    | (Weekly Cycle)     |
    | - New interactions  |
    | - Gap filling       |
    | - Distillation data |
    | - Quality gate      |
    +--------------------+
```

---

## 11. What Makes This Unique

This isn't just fine-tuning a model — it's creating a **living, learning personal AI**:

1. **Multi-source learning**: Absorbs knowledge from Claude, Gemini, Codex, and Qwen simultaneously
2. **Contradiction resolution**: When LLMs disagree, Jarvis uses its knowledge graph to resolve
3. **Owner-specific optimization**: Every training cycle makes Jarvis better for Conner specifically
4. **Progressive autonomy**: Starts needing cloud LLMs heavily, gradually becomes self-sufficient
5. **Quality guarantee**: Never deploys a worse model (automated A/B testing + rollback)
6. **Privacy-first**: All training data stays local, PII filtered, no data leaves the machine

This will be the first personal AI that genuinely learns and improves from its owner's interactions across multiple LLM providers. No one else has built this at the individual level.

---

*This document will be updated as research from Gemini and Codex is incorporated.*
