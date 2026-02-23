Based on a comprehensive review of the `jarvis-memory-repo` architecture and current planning documents, here are the deep research recommendations for the next evolution of **Jarvis Unlimited**.

---

### Executive Summary: The "Living Engine" Strategy
To transition from a "reactive script" to a "proactive assistant," Jarvis must move toward a **Service-First Architecture** with a **Verifiable Research Pipeline**. The focus is on decoupling the "Brain" (LLM/Memory) from the "Nervous System" (I/O and Connectivity) to ensure 24/7 availability without memory leaks or system instability.

---

### 1. 24/7 On-Device Reliability (Windows + Mobile)
**Current Risk:** Foreground Python scripts are prone to accidental closure, OS-level "sleep" kills, and memory bloat.

*   **P0: Formal Windows Service Migration**
    *   **Recommendation:** Wrap the Python engine in a Windows Service using `NSSM` (Non-Sucking Service Manager) or `PyWin32`. This ensures Jarvis starts at boot (before login) and restarts automatically on crash.
    *   **Implementation:** Decouple the `jarvis_engine` into two processes:
        1.  **Core Service:** Low-footprint API/Router (FastAPI/Uvicorn) that stays under 100MB RAM.
        2.  **Worker Pool:** On-demand processes for heavy LLM inference or web scraping that can be "recycled" (killed and restarted) every 4 hours to prevent memory fragmentation.
    *   **Validation:** "The Kill Test": Manually end the `jarvis_engine` process tree; the watchdog must restore full API connectivity within 15 seconds.

*   **P1: Mobile-First "Heartbeat" & Wake-on-LAN (WoL)**
    *   **Recommendation:** Implement a "Keep-Alive" heartbeat between the Samsung S25 and the Desktop. If the Desktop is sleeping, the Mobile app triggers a WoL packet to wake the server for high-compute tasks.
    *   **Validation:** Trigger a complex query from mobile while the PC is in 'Sleep' mode; verify successful wake and response.

---

### 2. Autonomous Web Research Pipeline
**Current Risk:** Simple search results lead to hallucinations or ingestion of "SEO spam."

*   **P0: Multi-Stage "Search-Verify-Ingest" Flow**
    *   **Recommendation:** Move beyond simple query-to-answer.
        1.  **Query Expansion:** Generate 3-5 distinct search variations.
        2.  **Source Filtering:** Whitelist academic/technical domains (MDN, StackOverflow, arXiv) and blacklist known AI-generated content farms.
        3.  **Cross-Reference Logic:** Only ingest facts into `brain/facts.json` if they appear in ≥2 independent sources.
    *   **Tools:** Integrate **Firecrawl** or **Jina Reader** for "Clean Markdown" extraction, which significantly reduces token costs and hallucination compared to raw HTML.
    *   **Validation:** Research a "fictional fact" (e.g., a made-up tech acronym); verify Jarvis identifies it as "unverifiable" or "likely false" rather than ingesting it.

---

### 3. Voice Interaction Quality (Always-Listening)
**Current Risk:** High CPU usage from continuous STT and "false triggers" in a noisy room.

*   **P1: Hybrid VAD + Local Wake-Word**
    *   **Recommendation:** Use **OpenWakeWord** (Trained on "Jarvis") combined with a **Silero VAD** (Voice Activity Detector).
        *   *Logic:* VAD (Low Power) -> Is there a human voice? -> Yes -> Wake-Word Engine (Medium Power) -> Is it "Jarvis"? -> Yes -> Whisper (High Power) for STT.
    *   **P2: Contextual Circular Buffer**
        *   **Recommendation:** Maintain a rolling 5-second audio buffer. When the wake-word is detected, prepend the buffer to the stream. This allows the user to say "Jarvis, what was that?" and have Jarvis know what "that" was from the room's previous audio.
    *   **Validation:** Measure "Time to Action" (TTA). Success = <1.2s from the end of the user command to the start of agent execution.

---

### 4. Measuring Learning & Regression (The "Intelligence Dashboard")
**Current Risk:** System "drift" where new memory ingestion breaks old capabilities.

*   **P0: The "Golden Task" Evaluation Suite**
    *   **Recommendation:** Create a `tests/intelligence_eval.json` containing 50 static questions about the user's life, medications, and school.
    *   **Metric: Memory Retrieval Accuracy (MRA).** Run this suite daily. If accuracy drops below 95%, trigger a "Memory Audit" to find conflicting `records.jsonl` entries.
*   **P1: Self-Correction Log**
    *   **Recommendation:** Track the ratio of "User Corrections" to "Total Commands." A rising correction ratio is a P0 alert for model/prompt regression.
    *   **Validation:** Intentionally feed Jarvis a conflicting fact (e.g., "I moved to New York" vs old "I live in LA"); verify the `intelligence_dashboard.py` flags the contradiction for resolution.

---

### 5. High-Impact Optimizations
*   **P0: Semantic Cache (Response Latency)**
    *   **Recommendation:** Store embeddings of common queries (e.g., "What's my schedule?"). If a new query is >0.95 semantically similar, serve the cached answer (updated via background sync) instead of calling the LLM.
*   **P1: Tiered Inference (Desktop vs. Mobile)**
    *   **Recommendation:** 
        *   **Desktop:** Use local GGUF/EXL2 (Llama-3-8B) for private/offline tasks.
        *   **Mobile:** Route through a secure proxy to Gemini 1.5 Pro for complex reasoning when away from home.
*   **P2: Auto-Cleanup (Memory Hygiene)**
    *   **Recommendation:** Implement a "forgetting" algorithm for low-signal data. If a memory hasn't been accessed in 90 days and has a low "Importance Score," move it to `backups/archived_memory.zip`.

---

### Summary of Risks & Mitigations

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **Privacy Leak** | High | Ensure all web-research tools use a local proxy; scrub PII from queries sent to external Search APIs. |
| **SSD Wear** | Med | Move `events.jsonl` and high-frequency logs to a RAM Disk or buffered write system. |
| **"Brain Rot"** | High | Mandatory weekly "Snapshot & Sig" validation (already partially in your `memory_snapshots.py`). |

### Next Step Recommendation
Prioritize the **P0 Windows Service Migration**. Without a 100% reliable "Nervous System," the advanced "Brain" functions will be underutilized due to downtime.
