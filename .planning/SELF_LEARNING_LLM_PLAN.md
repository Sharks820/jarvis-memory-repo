# Jarvis Self-Learning LLM Brain — Comprehensive Plan

**Date:** 2026-03-13
**Goal:** Transform Jarvis from an LLM *consumer* into its own self-learning model that absorbs knowledge from every interaction with Claude, Gemini, Codex, Qwen, and other providers, eventually becoming increasingly self-sufficient.

**Hardware:** RTX 4060 Ti 8GB VRAM, 32GB RAM, Ryzen 7 5700 (8C/16T), Windows 11, ~2TB storage

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Approach 1: QLoRA Fine-Tuning](#2-approach-1-qlora-fine-tuning)
3. [Approach 2: Enhanced RAG + Personalization](#3-approach-2-enhanced-rag--personalization)
4. [Approach 3: Knowledge Distillation](#4-approach-3-knowledge-distillation)
5. [Approach 4: Hybrid RAFT](#5-approach-4-hybrid-raft-recommended)
6. [Approach 5: Continual Learning](#6-approach-5-continual-learning)
7. [Recommended Implementation Roadmap](#7-recommended-implementation-roadmap)
8. [Data Pipeline Design](#8-data-pipeline-design)
9. [Evaluation Framework](#9-evaluation-framework)
10. [Risk Analysis](#10-risk-analysis)

---

## 1. Executive Summary

The vision: Jarvis becomes its own LLM by learning from every interaction with cloud models. Rather than one giant leap, this is achieved through a layered strategy:

| Layer | Technique | What It Gives Jarvis | Timeline |
|-------|-----------|---------------------|----------|
| 1 | Enhanced RAG | Perfect factual recall from its own memory | 2-3 weeks |
| 2 | Personality Fine-Tune | Jarvis's unique voice, Conner's preferences | 2-3 weeks |
| 3 | Distillation | Cloud-quality reasoning in a local model | 3-4 weeks |
| 4 | RAFT Integration | RAG-aware fine-tuning for best of both | 2-3 weeks |
| 5 | Continual Learning | Keeps improving without forgetting | Ongoing |

**Bottom line:** On 8GB VRAM, Jarvis can fine-tune models up to 9B parameters using QLoRA 4-bit quantization. The Qwen3.5 9B model already running locally is an excellent base. A hybrid RAFT approach (fine-tuning + RAG + distillation) gives the best results.

---

## 2. Approach 1: QLoRA Fine-Tuning

### What It Is
Low-Rank Adaptation with 4-bit quantization. The base model stays frozen in 4-bit precision; only small adapter matrices (LoRA) are trained in 16-bit. This cuts VRAM usage by 70-80%.

### Hardware Feasibility — RTX 4060 Ti 8GB

| Model | Params | QLoRA VRAM | Fits? | Notes |
|-------|--------|------------|-------|-------|
| Qwen3.5 4B | 4B | ~3.5 GB | YES | Fast training, good for iteration |
| Qwen3.5 9B | 9B | ~6.5 GB | YES | Best quality/VRAM tradeoff |
| Llama 3.1 8B | 8B | ~6.5 GB | YES | Strong reasoning baseline |
| Gemma 3 4B | 4B | ~3.5 GB | YES | Google's efficient architecture |
| Qwen3.5 27B | 27B | ~18 GB | NO | Needs 24GB GPU |
| Phi-4 14B | 14B | ~10 GB | NO | Needs 12GB GPU |

**Recommended base:** Qwen3.5 9B (already deployed as `qwen3.5:latest` in Ollama) or Qwen3.5 4B for faster iteration cycles.

### Data Requirements

| Dataset Size | Training Quality | Notes |
|-------------|-----------------|-------|
| 500-1K examples | Minimal personalization | Learns tone/format only |
| 1K-5K examples | Good personalization | Learns preferences and patterns |
| 5K-10K examples | Strong adaptation | Starts to "feel" like Jarvis |
| 10K-50K examples | Deep specialization | Genuine domain expertise |
| 50K+ examples | Full personality transfer | Approaching self-sufficient |

Jarvis already ingests conversations via `ConversationLearningEngine`. The episodic memory store and knowledge graph contain the raw material.

### Tools & Libraries

**Primary: Unsloth** (best for single-GPU, 2-5x speedup)
- Supports Qwen3.5, Llama 4, Gemma 3, DeepSeek on RTX 4060 Ti
- Windows support via WSL2 (Ubuntu) — GPU passthrough works
- Docker option available for zero-config setup
- QLoRA 4-bit with dynamic quantization recovers accuracy loss vs LoRA 16-bit

**Alternative: LLaMA-Factory**
- Native Windows 10/11 support (no WSL needed)
- WebUI for no-code fine-tuning
- Supports LoRA, QLoRA, full fine-tuning, DPO, RLHF
- Good for experimentation before committing to a pipeline

**Conversion pipeline:**
1. Train with Unsloth/LLaMA-Factory → saves LoRA adapter (safetensors)
2. Convert adapter: `convert_lora_to_gguf.py` (from llama.cpp)
3. Import into Ollama via Modelfile: `FROM qwen3.5:latest` + `ADAPTER ./jarvis-lora.gguf`
4. Jarvis gateway routes to `jarvis-brain:latest` instead of base model

### Training Time Estimates (RTX 4060 Ti)

| Dataset | Model | Epochs | Est. Time |
|---------|-------|--------|-----------|
| 1K examples | Qwen3.5 4B | 3 | ~15 min |
| 5K examples | Qwen3.5 4B | 3 | ~1 hour |
| 1K examples | Qwen3.5 9B | 3 | ~30 min |
| 5K examples | Qwen3.5 9B | 3 | ~2.5 hours |
| 10K examples | Qwen3.5 9B | 3 | ~5 hours |
| 50K examples | Qwen3.5 9B | 2 | ~15 hours |

### Training Configuration

```python
# Unsloth QLoRA config for RTX 4060 Ti 8GB
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3.5-9B-bnb-4bit",
    max_seq_length=2048,       # 8GB limits context
    load_in_4bit=True,
    dtype=None,                # auto-detect
)

model = FastLanguageModel.get_peft_model(
    model,
    r=32,                      # LoRA rank (32 fits 8GB)
    lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_dropout=0.05,
    bias="none",
    use_gradient_checkpointing="unsloth",  # 30% less VRAM
)

trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    max_seq_length=2048,
    per_device_train_batch_size=1,    # Must be 1 for 8GB
    gradient_accumulation_steps=8,     # Effective batch = 8
    learning_rate=2e-4,
    num_train_epochs=3,
    fp16=True,
    logging_steps=10,
    save_strategy="epoch",
)
```

### Quality Expectations
- **Personality/tone:** 80-90% match to desired Jarvis personality after 5K examples
- **Factual accuracy:** No improvement (LoRA doesn't add new knowledge reliably)
- **Reasoning:** Retains base model reasoning; slight degradation possible if overtrained
- **Best for:** Teaching Jarvis HOW to respond, not WHAT to know

### Implementation Complexity
**2-3 weeks** for a working fine-tuning pipeline with automated data export and Ollama import.

---

## 3. Approach 2: Enhanced RAG + Personalization

### What It Is
Instead of baking knowledge into model weights, retrieve it at query time from Jarvis's memory stores. This is the fastest path to making Jarvis "know" things.

### Current Jarvis RAG Infrastructure (Already Built)

Jarvis already has significant RAG infrastructure:

- **EmbeddingService** (`memory/embeddings.py`): nomic-embed-text-v1.5, 768-dim, batched with LRU cache
- **Hybrid search** (`memory/search.py`): FTS5 keyword + sqlite-vec semantic search
- **Knowledge graph** (`knowledge/graph.py`): SQLite-persisted fact nodes with NetworkX traversal
- **Memory consolidation** (`learning/consolidator.py`): Clusters episodic → semantic facts
- **Conversation state** (`conversation_state.py`): Cross-provider entity/decision/goal tracking

### What's Missing for "Brain-Like" RAG

| Component | Current | Needed | Effort |
|-----------|---------|--------|--------|
| Dynamic few-shot selection | None | Select similar past Q&A pairs as examples | 3-4 days |
| Personality conditioning | Basic system prompt | Learned personality vector from interaction patterns | 3-4 days |
| KG-augmented context | Fact injection exists | Structured fact chains with confidence scoring | 2-3 days |
| Adaptive retrieval depth | Fixed top-K | Query complexity → retrieval depth mapping | 2 days |
| Source attribution | None | Track which memory informed each response | 2 days |
| Response quality scoring | Implicit feedback only | Auto-score and feed back to retrieval weights | 3 days |

### Dynamic Few-Shot Example Selection

The most impactful upgrade. For every incoming query:

1. Embed the query with nomic-embed-text-v1.5
2. Search episodic memory for similar past conversations (user question + assistant response pairs)
3. Select top 3-5 most similar interactions as few-shot examples
4. Inject them into the prompt before the current query

This makes the model respond as if it "remembers" how it handled similar situations — without any fine-tuning.

```python
# Pseudocode for dynamic few-shot RAG
class JarvisBrainRAG:
    def build_prompt(self, user_query: str) -> list[dict]:
        # 1. Retrieve relevant facts from KG
        facts = self.kg.query_relevant_facts(user_query, limit=10)

        # 2. Retrieve similar past conversations
        similar_convos = self.memory.hybrid_search(
            user_query, kind="episodic",
            tags=["conversation"], limit=5
        )

        # 3. Retrieve user preferences
        prefs = self.preference_tracker.get_active_preferences()

        # 4. Build personality-conditioned system prompt
        system = self._build_system_prompt(prefs, facts)

        # 5. Inject few-shot examples
        messages = [{"role": "system", "content": system}]
        for convo in similar_convos:
            messages.append({"role": "user", "content": convo.user_msg})
            messages.append({"role": "assistant", "content": convo.assistant_msg})

        # 6. Add current query
        messages.append({"role": "user", "content": user_query})
        return messages
```

### Hardware Requirements
- **Training:** None (RAG is inference-only)
- **Inference:** Same as current Qwen3.5 9B inference (~6GB VRAM)
- **Storage:** Embedding index grows ~1KB per memory record; negligible

### Data Requirements
- Works with ANY amount of data (even zero — gracefully degrades to base model)
- Improves linearly with more conversation history
- Already have data flowing via `ConversationLearningEngine`

### Quality Expectations
- **Factual accuracy:** 90%+ when facts are in memory (retrieval quality is the bottleneck)
- **Personality:** 60-70% (limited by prompt engineering ceiling)
- **Reasoning:** 100% of base model (no degradation)
- **Best for:** Making Jarvis know things; less effective for personality/style

### Implementation Complexity
**2-3 weeks** for the full enhanced RAG pipeline. Builds on existing infrastructure.

---

## 4. Approach 3: Knowledge Distillation

### What It Is
Use cloud models (Claude, Gemini, Codex) as "teachers" to generate high-quality training data, then fine-tune the local model on that data. The local model learns to mimic the reasoning quality of much larger models.

### Distillation Strategies

#### Strategy A: Response Distillation (Simplest)
1. Collect Jarvis's real queries from conversation history
2. Send each query to Claude/Gemini/Codex and collect responses
3. Fine-tune local Qwen3.5 on (query, best_response) pairs

**Pros:** Simple, effective for response quality
**Cons:** Requires cloud API calls, potential ToS issues with some providers

#### Strategy B: Chain-of-Thought Distillation
1. Ask teacher model to show its reasoning step-by-step
2. Include the reasoning chain in training data
3. Local model learns to reason, not just answer

```json
{
  "instruction": "What medication interactions should I check for aspirin?",
  "reasoning": "Let me think through this systematically...\n1. Aspirin is an NSAID and antiplatelet...\n2. Key interactions include...\n3. Risk factors to consider...",
  "response": "Key aspirin interactions to check: [structured answer]"
}
```

**Pros:** Transfers reasoning capability, not just answers
**Cons:** More expensive (longer responses), needs careful curation

#### Strategy C: Progressive Distillation (Most Sophisticated)
1. Start with large teacher (Claude Opus) → train medium student
2. Use medium student as teacher → train small student
3. Each stage preserves 90%+ of capability at lower cost

**Pros:** Best quality preservation
**Cons:** Complex pipeline, multiple training rounds

#### Strategy D: Synthetic Data Generation
1. Use teacher models to generate diverse training scenarios
2. Create "textbook-quality" synthetic conversations about Conner's domains
3. Filter for quality, deduplicate, and fine-tune

From Microsoft's research: "High-quality training data matters more for small language models than sheer quantity." Their Phi-3 series was distilled from larger models, retaining 90%+ capability at 5% of the size.

### Optimal Compression Order (2025 Research)
Recent studies found that **Pruning → Distillation → Quantization (P-KD-Q)** yields the best compression with preserved capabilities. For Jarvis:
1. Start with Qwen3.5 9B (already quantized for inference)
2. Distill knowledge from cloud models into it via fine-tuning
3. Quantize the result back to Q4_K_M for Ollama deployment

### Hardware Requirements
- **Data generation:** Cloud API calls (cost varies; ~$5-20 for 10K examples with Claude Haiku)
- **Training:** Same as QLoRA fine-tuning (6.5GB VRAM for 9B model)
- **Inference:** Same as current setup

### Data Requirements
- 5K-10K distilled examples for meaningful quality improvement
- 10K-50K for strong domain expertise transfer
- Cost: ~$2-10 per 10K examples using Claude Haiku/Gemini Flash for generation

### Quality Expectations
- **Reasoning quality:** 70-85% of teacher model quality
- **Domain expertise:** 80-90% for well-covered domains
- **Personality:** Can be combined with personality fine-tuning
- **Best for:** Closing the quality gap between local and cloud models

### Implementation Complexity
**3-4 weeks** including data generation pipeline, quality filtering, and training.

---

## 5. Approach 4: Hybrid RAFT (RECOMMENDED)

### What It Is
RAFT (Retrieval-Augmented Fine-Tuning) combines the best of RAG and fine-tuning. The model is fine-tuned to be aware of retrieval, learning to extract relevant information from retrieved documents while ignoring distractors.

This is the **recommended primary approach** for Jarvis because it directly addresses the core need: a model that uses its own memory system effectively.

### How RAFT Works for Jarvis

Traditional RAG: Model receives retrieved docs in prompt → hopes it uses them correctly.
RAFT: Model is *trained* on examples with retrieved docs → learns to extract and cite from them.

Each training example contains:
1. **Question (Q):** A real user query from Jarvis history
2. **Oracle document (D*):** The memory record that actually answers the question
3. **Distractor documents (Di):** Irrelevant memory records retrieved alongside the oracle
4. **Chain-of-thought answer (A*):** The correct answer that cites the oracle document

```json
{
  "question": "When is Conner's next dentist appointment?",
  "documents": [
    {"relevant": true, "content": "Conner has dentist appointment March 28 2026 at 2pm with Dr. Smith"},
    {"relevant": false, "content": "Conner prefers morning appointments when possible"},
    {"relevant": false, "content": "Dr. Smith's office is at 123 Main St"},
    {"relevant": false, "content": "Conner's last eye exam was January 2026"}
  ],
  "answer": "Based on the scheduling record, Conner's next dentist appointment is March 28, 2026 at 2:00 PM with Dr. Smith. <quote>dentist appointment March 28 2026 at 2pm with Dr. Smith</quote>"
}
```

### Why RAFT Is Ideal for Jarvis

1. **Jarvis already has the retrieval system** — hybrid search, KG facts, embeddings are built
2. **Training data can be auto-generated** — use existing Q&A history + memory records
3. **Teaches the model to trust its own memory** rather than hallucinating
4. **Distractor training** prevents the model from being confused by irrelevant retrievals
5. **Citation behavior** makes responses verifiable and debuggable

### Implementation Plan

#### Phase 1: Data Generation Pipeline (1 week)

```python
class RAFTDataGenerator:
    """Generate RAFT training data from Jarvis's memory and conversation history."""

    def generate_example(self, query: str, memory_engine, kg) -> dict:
        # 1. Find the actual answer in memory (oracle doc)
        results = memory_engine.hybrid_search(query, limit=20)
        oracle = self._find_best_match(query, results)

        # 2. Sample distractor docs (real but irrelevant memories)
        distractors = self._sample_distractors(results, oracle, count=3)

        # 3. Generate CoT answer using cloud model (teacher)
        cot_answer = self._generate_cot_answer(
            query, oracle, teacher_model="claude-haiku"
        )

        # 4. Shuffle document order (prevent position bias)
        docs = [oracle] + distractors
        random.shuffle(docs)

        return {
            "question": query,
            "documents": docs,
            "answer": cot_answer,
            "oracle_idx": docs.index(oracle),
        }
```

#### Phase 2: Fine-Tuning (1 week)

Train Qwen3.5 9B (or 4B for fast iteration) with QLoRA on RAFT examples:
- Format: Documents concatenated in prompt → model generates answer with citations
- LoRA rank 32, all linear layers targeted
- 3 epochs over 5K-10K examples
- Gradient checkpointing enabled for 8GB VRAM

#### Phase 3: Integration (1 week)

1. Convert LoRA adapter to GGUF
2. Create Ollama Modelfile with Jarvis system prompt
3. Update `ModelGateway` to route to `jarvis-brain:latest`
4. Modified inference: retrieve → inject docs → generate with RAFT model
5. Fallback: if RAFT model confidence is low, escalate to cloud

### Hardware Requirements
- **Training:** 6.5GB VRAM (QLoRA 4-bit, Qwen3.5 9B) — FITS
- **Inference:** Same as current Qwen3.5 inference
- **Data generation:** Cloud API costs for teacher model (~$5-15)

### Data Requirements
- **Minimum:** 2K RAFT examples (query + docs + answer)
- **Good:** 5K-10K examples covering Jarvis's main use cases
- **Sources:** Conversation history, KG facts, memory records, synthetic scenarios

### Quality Expectations
- **RAG accuracy:** 85-95% (model trained to extract from its own memory format)
- **Hallucination rate:** Significantly reduced vs base RAG (trained to cite sources)
- **Personality:** Moderate (can be enhanced with personality examples in training data)
- **Reasoning:** 80-90% of base model (slight trade-off for retrieval awareness)

### Implementation Complexity
**2-3 weeks** for core RAFT pipeline. Builds on existing memory/retrieval infrastructure.

---

## 6. Approach 5: Continual Learning

### What It Is
The model keeps improving from each interaction without periodic retraining from scratch. This is the long-term vision: Jarvis gets smarter every day.

### Catastrophic Forgetting — The Core Challenge

When you fine-tune a model on new data, it tends to forget what it knew before. Recent research (2025-2026) offers several solutions:

#### Strategy A: Elastic Weight Consolidation (EWC)
- Identifies which model weights are important for existing knowledge
- Penalizes changes to those weights during new training
- **Jarvis implementation:** After each fine-tuning round, compute Fisher information matrix for current adapter weights; constrain future updates

#### Strategy B: Source-Shielded Updates (SSU) — 2025 Research
- Scores parameter importance for preserving source capabilities
- Freezes critical parameters column-wise before adaptation
- **Best for Jarvis:** Protects core reasoning while allowing personality/knowledge updates

#### Strategy C: LoRA Adapter Stacking
- Instead of retraining one adapter, train a new small adapter for each knowledge domain
- Merge adapters at inference time using weighted combination
- **Jarvis implementation:**
  - `jarvis-personality.gguf` — tone, style, preferences
  - `jarvis-medical.gguf` — health/medication knowledge
  - `jarvis-schedule.gguf` — calendar/planning patterns
  - `jarvis-family.gguf` — family relationship knowledge
  - Merge with configurable weights at inference

#### Strategy D: Replay Buffer
- Maintain a curated set of "golden" examples from past training
- Mix old examples with new examples during each training round (e.g., 30% old + 70% new)
- Prevents forgetting by reminding the model of previous knowledge
- **Jarvis implementation:** `golden_training_set.jsonl` that grows over time

#### Strategy E: SA-SFT (Self-Augmentation)
- Before fine-tuning on new data, the model generates "self-dialogues" about its existing knowledge
- Mix self-generated data with new training data
- Lightweight way to preserve capabilities without external data
- **Ideal for Jarvis:** Self-dialogue generation uses only local compute

### Continual Learning Pipeline for Jarvis

```
Daily cycle:
1. Daemon collects conversation pairs from the day
2. Filter for knowledge-bearing interactions (existing _is_knowledge_bearing())
3. Generate RAFT training examples from new conversations
4. Mix with replay buffer (30% old golden examples)
5. Run QLoRA fine-tuning (incremental, ~15-30 min for 100-500 new examples)
6. Convert updated adapter → GGUF → Ollama import
7. Hot-swap model: jarvis-brain:latest → jarvis-brain:v{date}
8. Validate against golden test set (catch catastrophic forgetting)
9. If validation fails, rollback to previous adapter version

Weekly cycle:
1. Full consolidation pass (MemoryConsolidator)
2. Regenerate RAFT examples from consolidated facts
3. Retrain from scratch on full curated dataset + replay buffer
4. Update golden test set with new critical examples
```

### Hardware Requirements
- **Daily incremental training:** ~15-30 min on RTX 4060 Ti (100-500 examples)
- **Weekly full retrain:** ~2-5 hours (5K-10K examples)
- **Storage:** ~2GB per adapter version; keep last 7 versions = ~14GB

### Quality Expectations
- **Knowledge accumulation:** Model genuinely improves over weeks/months
- **Forgetting risk:** Manageable with replay buffer + validation
- **Personality stability:** High (personality adapter is rarely retrained)
- **Long-term ceiling:** Approaches 80-90% of cloud model quality for Jarvis-specific tasks

### Implementation Complexity
**Ongoing after initial setup (4-6 weeks)**. Requires:
- Automated data pipeline (1 week)
- Training orchestration (1 week)
- Validation framework (1 week)
- Model versioning and rollback (3-4 days)
- Monitoring dashboard (3-4 days)

---

## 7. Recommended Implementation Roadmap

### Phase 1: Foundation (Weeks 1-3) — Enhanced RAG

**Goal:** Make Jarvis's existing memory system work dramatically better without any fine-tuning.

| Task | Description | Days |
|------|------------|------|
| 1A | Dynamic few-shot example selection from conversation history | 3 |
| 1B | Personality conditioning from learned preferences | 3 |
| 1C | Adaptive retrieval depth (simple → deep based on query complexity) | 2 |
| 1D | KG fact chain injection with confidence scoring | 2 |
| 1E | Response source attribution ("I know this because...") | 2 |
| 1F | Quality scoring + feedback loop to retrieval weights | 3 |

**Deliverable:** `engine/src/jarvis_engine/brain/` subpackage with `JarvisBrainRAG` class.
**Quality gate:** Jarvis answers 80%+ of personal questions correctly from memory.

### Phase 2: Fine-Tuning Pipeline (Weeks 3-5) — Training Infrastructure

**Goal:** Build the machinery to fine-tune and deploy custom models.

| Task | Description | Days |
|------|------------|------|
| 2A | WSL2 + Unsloth installation and validation | 1 |
| 2B | Conversation data export pipeline (memory → training JSONL) | 3 |
| 2C | Data quality filtering and deduplication | 2 |
| 2D | QLoRA training script with config management | 2 |
| 2E | LoRA → GGUF → Ollama import automation | 2 |
| 2F | Gateway routing to custom model with fallback | 2 |
| 2G | Basic evaluation harness (accuracy, personality, regression) | 2 |

**Deliverable:** `scripts/train-jarvis-brain.py`, `scripts/deploy-jarvis-brain.sh`
**Quality gate:** Successfully fine-tune and deploy a model; verify no VRAM overflow.

### Phase 3: RAFT Training (Weeks 5-7) — The Brain

**Goal:** Train Jarvis to use its own memory system like a brain uses long-term memory.

| Task | Description | Days |
|------|------------|------|
| 3A | RAFT data generator (queries + oracle/distractor docs + CoT answers) | 4 |
| 3B | Teacher model integration (Claude Haiku/Gemini Flash for CoT generation) | 2 |
| 3C | RAFT fine-tuning with citation training | 3 |
| 3D | Integration with enhanced RAG pipeline (Phase 1 output) | 2 |
| 3E | A/B comparison: RAFT model vs base model on Jarvis tasks | 2 |

**Deliverable:** `jarvis-brain:v1` model deployed in Ollama.
**Quality gate:** RAFT model outperforms base Qwen3.5 on memory-grounded questions by 15%+.

### Phase 4: Distillation (Weeks 7-9) — Quality Boost

**Goal:** Transfer reasoning quality from cloud models into the local brain.

| Task | Description | Days |
|------|------------|------|
| 4A | Query harvesting from conversation history (diverse, representative set) | 2 |
| 4B | Multi-teacher response generation (Claude, Gemini, Codex) | 3 |
| 4C | Response ranking and quality filtering | 2 |
| 4D | CoT distillation training (reasoning chain transfer) | 3 |
| 4E | DPO alignment (preferred vs rejected response pairs from actual usage) | 3 |

**Deliverable:** `jarvis-brain:v2` with distilled reasoning capabilities.
**Quality gate:** Jarvis handles complex multi-step queries without cloud escalation 70%+ of the time.

### Phase 5: Continual Learning (Weeks 9-12) — Self-Improvement

**Goal:** Jarvis improves automatically from every interaction.

| Task | Description | Days |
|------|------------|------|
| 5A | Daily data collection and RAFT example generation | 3 |
| 5B | Incremental QLoRA training with replay buffer | 3 |
| 5C | Automated model deployment with hot-swap | 2 |
| 5D | Golden test set and regression detection | 3 |
| 5E | Model version management and rollback | 2 |
| 5F | Forgetting prevention (EWC or adapter stacking) | 3 |
| 5G | Dashboard: training metrics, quality trends, cloud escalation rate | 3 |

**Deliverable:** Fully automated learning loop; Jarvis gets smarter daily.
**Quality gate:** Cloud escalation rate decreases 5%+ per month; no regression on golden tests.

---

## 8. Data Pipeline Design

### Data Sources (Already Available in Jarvis)

| Source | Module | Data Type | Est. Volume |
|--------|--------|-----------|-------------|
| Conversation history | `learning/engine.py` | User Q + Assistant A pairs | Growing daily |
| Episodic memories | `memory/store.py` | Timestamped event records | Thousands |
| Semantic facts | `learning/consolidator.py` | Consolidated knowledge | Hundreds |
| Knowledge graph | `knowledge/graph.py` | Entity-relation-entity triples | Hundreds |
| User preferences | `learning/preferences.py` | Key-value preference pairs | Dozens |
| Corrections | `learning/correction_detector.py` | Old claim → New claim | Dozens |
| Usage patterns | `learning/usage_patterns.py` | Route/topic frequency | Continuous |
| Conversation state | `conversation_state.py` | Entities, goals, decisions | Per-session |

### Training Data Formats

#### SFT Format (Supervised Fine-Tuning)
```json
{
  "conversations": [
    {"role": "system", "content": "You are Jarvis, Conner's personal AI assistant..."},
    {"role": "user", "content": "What time is my dentist appointment?"},
    {"role": "assistant", "content": "Your dentist appointment is March 28 at 2:00 PM with Dr. Smith."}
  ]
}
```

#### RAFT Format (Retrieval-Augmented Fine-Tuning)
```json
{
  "instruction": "Answer the question using the provided documents. Cite relevant sources.",
  "documents": ["[Doc1] ...", "[Doc2] ...", "[Doc3 - relevant] ..."],
  "question": "What time is my dentist appointment?",
  "answer": "Based on [Doc3], your dentist appointment is March 28 at 2:00 PM with Dr. Smith."
}
```

#### DPO Format (Preference Optimization)
```json
{
  "prompt": "What should I have for dinner tonight?",
  "chosen": "Based on your preferences, how about grilled chicken with roasted vegetables? You mentioned enjoying that last week, and you have chicken in the fridge according to your grocery list.",
  "rejected": "I don't have enough information to suggest dinner. You could try searching for recipes online."
}
```

### Data Export Pipeline

New module: `engine/src/jarvis_engine/brain/data_export.py`

```python
class TrainingDataExporter:
    """Export Jarvis memory and conversations into training-ready formats."""

    def export_sft(self, output_path: str, min_quality: float = 0.7) -> int:
        """Export conversation pairs as SFT training data."""

    def export_raft(self, output_path: str, docs_per_example: int = 4) -> int:
        """Export RAFT examples with oracle + distractor documents."""

    def export_dpo(self, output_path: str) -> int:
        """Export DPO pairs from feedback tracker data."""

    def export_golden_set(self, output_path: str, count: int = 100) -> int:
        """Export curated golden test examples for regression testing."""
```

---

## 9. Evaluation Framework

### Metrics

| Metric | What It Measures | Target |
|--------|-----------------|--------|
| **Memory Recall** | Can Jarvis answer from its own memory? | 85%+ |
| **Factual Accuracy** | Are memory-grounded answers correct? | 90%+ |
| **Personality Score** | Does it sound like Jarvis? (tone, style, preferences) | 80%+ |
| **Reasoning Quality** | Can it handle multi-step questions? | 75%+ of cloud |
| **Citation Accuracy** | Does it cite the right memory source? | 85%+ |
| **Cloud Escalation Rate** | How often does it need cloud fallback? | <30% of queries |
| **Forgetting Rate** | Does it lose previously-known facts? | <5% per update |
| **Response Latency** | Time to respond with local brain | <3s for simple |

### Golden Test Set

Maintain a curated set of 100-200 test queries covering:
- Personal facts about Conner and family
- Scheduling and calendar queries
- Medical/prescription information
- Preference-based recommendations
- Multi-step reasoning tasks
- Edge cases and previously-failed queries

Run after every model update. Any regression > 5% triggers rollback.

### A/B Testing

Route 10% of queries to both base Qwen3.5 and jarvis-brain; compare:
- Response relevance (auto-scored by embedding similarity to ground truth)
- User corrections (implicit negative feedback)
- Cloud escalation triggers

---

## 10. Risk Analysis

### Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| VRAM overflow during training | HIGH | Strict QLoRA config; batch_size=1; gradient checkpointing |
| Catastrophic forgetting | HIGH | Replay buffer; golden test validation; adapter stacking |
| Training data quality | MEDIUM | Quality filtering; human spot-checking; cloud teacher verification |
| Model quality regression | MEDIUM | Golden test set; automatic rollback; A/B testing |
| WSL2 GPU passthrough issues | LOW | Docker fallback; native Windows via LLaMA-Factory |
| Ollama import failures | LOW | GGUF format is well-tested; keep base model as fallback |

### Ethical/Legal Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Cloud model ToS on distillation | MEDIUM | Use for personal use only; prefer open models as teachers |
| PII in training data | MEDIUM | Existing PII masking (phone numbers); extend to training export |
| Model generating harmful content | LOW | Jarvis is single-user; base model safety training preserved |

### Resource Costs

| Resource | One-Time | Monthly |
|----------|----------|---------|
| Cloud API for distillation (10K examples) | $5-20 | — |
| Cloud API for continual CoT generation | — | $2-5 |
| Storage (model versions) | — | ~2GB/week |
| Training compute (electricity) | — | ~$1-2 |
| Developer time | 8-12 weeks | 2-4 hrs/week maintenance |

---

## Appendix A: Tool Installation Guide

### WSL2 + Unsloth Setup (Windows 11)

```bash
# 1. Install WSL2 with Ubuntu
wsl --install -d Ubuntu-24.04

# 2. Configure WSL memory (Windows side: %USERPROFILE%/.wslconfig)
# [wsl2]
# memory=24GB
# processors=8
# swap=4GB

# 3. Inside WSL2: Install CUDA toolkit
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update && sudo apt-get -y install cuda-toolkit-12-4

# 4. Install Unsloth
pip install unsloth[cu124-torch250]

# 5. Verify GPU access
python -c "import torch; print(torch.cuda.get_device_name(0))"
```

### LLaMA-Factory Setup (Native Windows — Alternative)

```bash
# Native Windows (no WSL needed)
pip install llamafactory

# Launch WebUI
llamafactory-cli webui
```

### Ollama Model Import

```bash
# 1. Convert LoRA adapter to GGUF (from llama.cpp repo)
python convert_lora_to_gguf.py \
    --base unsloth/Qwen3.5-9B \
    --adapter ./jarvis-lora \
    --outfile jarvis-lora.gguf

# 2. Create Modelfile
cat > Modelfile << 'EOF'
FROM qwen3.5:latest
ADAPTER ./jarvis-lora.gguf

SYSTEM """You are Jarvis, Conner's personal AI assistant. You have deep knowledge
of Conner's life, preferences, schedule, and family. You are direct, efficient,
and proactive. You cite your memory sources when answering factual questions."""

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
EOF

# 3. Create and test
ollama create jarvis-brain -f Modelfile
ollama run jarvis-brain "What do you know about me?"
```

---

## Appendix B: Key Research References

- **RAFT** (UC Berkeley, 2024): Retrieval-Augmented Fine-Tuning for domain-specific RAG
- **MiniLLM** (2023): Knowledge distillation using reverse KL divergence
- **EasyDistill** (ModelScope): Toolkit for LLM knowledge distillation
- **Source-Shielded Updates** (2025): Parameter importance scoring for continual learning
- **SA-SFT** (2025): Self-augmentation to prevent catastrophic forgetting
- **Nested Learning** (Google, 2025): Models as nested optimization problems
- **P-KD-Q ordering** (2025): Optimal compression sequence for LLMs
- **GRPO/DAPO** (2026): Post-training RL techniques beyond DPO

---

## Appendix C: Jarvis Architecture Integration Points

The self-learning brain integrates with existing Jarvis modules:

```
                    ┌─────────────────────────────┐
                    │     jarvis-brain:latest      │
                    │   (RAFT fine-tuned Qwen3.5)  │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │      JarvisBrainRAG          │
                    │  (Dynamic few-shot + KG +    │
                    │   personality conditioning)  │
                    └──────────┬──────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                     │
  ┌───────▼───────┐   ┌───────▼───────┐   ┌────────▼──────┐
  │ MemoryEngine  │   │ KnowledgeGraph│   │ PreferenceTrack│
  │ (hybrid_search│   │ (fact chains) │   │ (user prefs)   │
  │  + embeddings)│   │               │   │                │
  └───────────────┘   └───────────────┘   └────────────────┘
          │
  ┌───────▼───────┐
  │ ConversationLearningEngine │──→ TrainingDataExporter
  │ (ingests every interaction)│──→ RAFTDataGenerator
  └────────────────────────────┘──→ ContinualTrainingLoop
```

**Gateway modification** (`gateway/models.py`):
- New model routing: `jarvis-brain` as primary, `qwen3.5:latest` as fallback, cloud as last resort
- Confidence-based escalation: if jarvis-brain response confidence < threshold, retry with base or cloud
- All cloud responses are captured and fed back into the training pipeline

**New modules to create:**
- `engine/src/jarvis_engine/brain/` — Brain subpackage
  - `rag.py` — Enhanced RAG with few-shot and personality
  - `data_export.py` — Training data pipeline
  - `raft_generator.py` — RAFT example generation
  - `trainer.py` — Training orchestration (calls Unsloth via subprocess)
  - `evaluator.py` — Golden test set evaluation
  - `versioning.py` — Model version management and rollback
