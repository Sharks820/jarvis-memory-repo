# Model Stack Deep Dive (2026-02-22)

## Hardware Constraint Snapshot (Local Verification)
- GPU: NVIDIA GeForce RTX 4060 Ti, 8188 MiB VRAM
- RAM: 34,248,024,064 bytes (~32 GB)
- CPU: AMD Ryzen 7 5700 (8C/16T)

This is a strong consumer setup, but the 8 GB VRAM is still the hard bottleneck.

## Cross-Model Advisor Input
- Claude input: useful directionally for routing and stack design, but it mixed in non-primary citation links.
- Gemini input: useful for workflow framing, but recommendations leaned older (Qwen2/Mistral-era emphasis).

Decision rule used here: keep only claims backed by primary sources and your local runtime checks.

## What You Can Run Best Right Now

### Local Benchmark Snapshot (Warm Run, This PC)
- Prompt: "Reply exactly with ten words about deterministic testing."
- `qwen3:latest` (8B class): ~49.72 tok/s
- `deepseek-r1:8b`: ~47.96 tok/s
- `qwen3:4b`: ~88.83 tok/s
- `qwen3:14b`: ~10.25 tok/s
- `qwen3-coder:30b`: ~21.42 tok/s
- `gemma3:12b`: ~13.73 tok/s
- `deepseek-r1:32b`: ~2.35 tok/s

### Tier 1 (Best practical local core)
1. `qwen3:latest` (8B class via Ollama)
   - Strongest balance of capability, tool-use behavior, and speed on this exact machine.
2. `deepseek-r1:8b` (Ollama)
   - Best "hard reasoning" swap-in at similar speed class.
3. `qwen3:14b` (Ollama)
   - Higher-capability option when you can accept materially lower throughput.
4. `qwen3-coder:30b` (Ollama)
   - Useful specialist for heavier coding passes; not ideal as always-on default on 8 GB VRAM systems.
5. `qwen3:4b` (Ollama)
   - Very fast fallback for lightweight steps and orchestration.
6. `gemma3:12b` (Ollama)
   - Multimodal option; slower than 8B class models on this hardware.

### Tier 2 (High-value additions via Hugging Face + llama.cpp/vLLM)
1. `Qwen/Qwen3-8B`
   - 8.2B, native 32,768 context, 131,072 with YaRN.
   - Supports thinking/non-thinking control and strong agent/tool guidance.
2. `Qwen/Qwen2.5-Coder-7B-Instruct`
   - Strong coding model family with 7B/14B/32B options and long-context support.
3. `nvidia/NVIDIA-Nemotron-Nano-9B-v2`
   - 9B class reasoning/coding model with published benchmark deltas over Qwen3-8B.
4. `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B`
   - Distilled reasoning model with strong benchmark profile; best used with CPU+GPU hybrid/offload on this machine.

## Models To Avoid On This Exact Hardware (as primary interactive model)
1. `deepseek-r1:32b` as an always-on primary model (too slow interactively on this box at ~2.35 tok/s warm).
2. `qwen3-coder:480b` local mode (Ollama docs state ~250 GB memory requirement).
3. Any 30B+ dense model as always-on single-model runtime when low latency is required.

## Recommended Runtime Architecture (Most Powerful Practical)

### Inference Layer
1. Primary runtime: Ollama for fast model switching and local ops.
2. Power runtime: llama.cpp for custom GGUF control and CPU+GPU hybrid tuning.
3. Optional serving scale later: vLLM when you need API-style serving/concurrency.

### Model Routing
1. Primary planner + daily driver: `qwen3:latest`
2. Hard-reasoning escalation: `deepseek-r1:8b`
3. Higher-capability planning/coding pass: `qwen3:14b`
4. Specialist coder pass: `qwen3-coder:30b`
5. Deep batch reasoning (non-interactive): `deepseek-r1:32b`
6. Multimodal checks: `gemma3:12b`

### Learning/Fine-Tuning Layer
1. Primary: Unsloth (QLoRA/RL workflows optimized for lower VRAM).
2. Control panel/coverage: LLaMA-Factory (broad model/method support and UI/CLI flows).
3. Keep no-regression gate mandatory before promotion into production routing.

## Immediate Pull/Install Plan
```powershell
# Core Ollama set (installed)
ollama pull qwen3:latest
ollama pull deepseek-r1:8b
ollama pull qwen3:4b

# High-capability options (installed)
ollama pull qwen3:14b
ollama pull qwen3-coder:30b

# Optional extras
ollama pull gemma3:12b
ollama pull deepseek-r1:32b

# For coder-heavy role (HF route for training/fine-tune workflows)
# Use Hugging Face + llama.cpp/vLLM for:
# - Qwen/Qwen2.5-Coder-7B-Instruct
# - nvidia/NVIDIA-Nemotron-Nano-9B-v2
```

## Sources
- Ollama DeepSeek-R1 library: https://ollama.com/library/deepseek-r1
- Ollama Gemma3 library: https://ollama.com/library/gemma3
- Ollama Qwen3 library: https://ollama.com/library/qwen3
- Ollama Qwen3-Coder library: https://ollama.com/library/qwen3-coder
- Qwen3-8B model card: https://huggingface.co/Qwen/Qwen3-8B
- Qwen2.5-Coder-7B model card: https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct
- DeepSeek-R1-Distill-Qwen-14B model card: https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B
- NVIDIA Nemotron Nano 9B v2 model card: https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-9B-v2
- Gemma 3 announcement: https://blog.google/technology/developers/gemma-3/
- Gemma-3-12B model card: https://huggingface.co/google/gemma-3-12b-it
- llama.cpp repo: https://github.com/ggml-org/llama.cpp
- vLLM repo: https://github.com/vllm-project/vllm
- Unsloth repo: https://github.com/unslothai/unsloth
- LLaMA-Factory repo: https://github.com/hiyouga/LLaMA-Factory
- Open WebUI repo: https://github.com/open-webui/open-webui
- OpenClaw docs: https://docs.openclaw.ai/
