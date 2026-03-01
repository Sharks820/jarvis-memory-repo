# Widget UX Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add typing indicator, help/tooltips, cold start optimization, forget command, onboarding, and clean response display to the Jarvis desktop widget.

**Architecture:** The widget is pure tkinter (no HTML/JS). Chat uses `tk.Text` with tag-based styling (`_log()` method). State machine: idle/listening/processing/error. Commands go through `/command` HTTP endpoint to mobile API, which calls `cmd_voice_run()` in-process. Responses are in `stdout_tail` as `response=...` lines.

**Tech Stack:** Python 3, tkinter, threading, existing CQRS command bus

---

### Task 1: Typing Indicator — Animated "Thinking" Tag + Insert/Remove

**Files:**
- Modify: `engine/src/jarvis_engine/desktop_widget.py:1278-1336` (add "thinking" tag)
- Modify: `engine/src/jarvis_engine/desktop_widget.py:1746-1797` (insert/remove indicator)
- Test: `engine/tests/test_main.py` (verify no regressions)

**Step 1: Add "thinking" chat tag in `_configure_chat_tags()`**

After the existing "timestamp" tag (line 1336), add:

```python
        self.output.tag_configure(
            "thinking",
            foreground="#ff9f43",
            font=("Consolas", 11, "italic"),
            lmargin1=8,
            lmargin2=8,
            rmargin=40,
            spacing1=4,
            spacing3=4,
        )
```

**Step 2: Add `_thinking_marker` instance variable**

In `__init__` area near line 714 (after `_widget_state`), add:

```python
        self._thinking_marker: str | None = None  # Text index of thinking indicator start
```

**Step 3: Add `_show_thinking()` and `_hide_thinking()` methods**

After `_log_async()` (line 1617), add:

```python
    def _show_thinking(self) -> None:
        """Insert animated 'Jarvis is thinking...' indicator in chat."""
        self.output.config(state=tk.NORMAL)
        self._thinking_marker = self.output.index(tk.END)
        self.output.insert(tk.END, "Jarvis is thinking...\n", "thinking")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
        # Also show in pop-out
        popout = getattr(self, "_popout_text", None)
        if popout is not None:
            try:
                popout.config(state=tk.NORMAL)
                popout.insert(tk.END, "Jarvis is thinking...\n", "thinking")
                popout.see(tk.END)
                popout.config(state=tk.DISABLED)
            except tk.TclError:
                pass
        self._animate_thinking()

    def _hide_thinking(self) -> None:
        """Remove the thinking indicator from chat."""
        if self._thinking_after_id is not None:
            try:
                self.after_cancel(self._thinking_after_id)
            except Exception:
                pass
            self._thinking_after_id = None
        if self._thinking_marker is not None:
            self.output.config(state=tk.NORMAL)
            try:
                self.output.delete(self._thinking_marker, tk.END)
            except tk.TclError:
                pass
            self.output.config(state=tk.DISABLED)
            # Also remove from pop-out
            popout = getattr(self, "_popout_text", None)
            if popout is not None:
                try:
                    popout.config(state=tk.NORMAL)
                    # Remove last line (the thinking indicator)
                    popout.delete("end-2l", tk.END)
                    popout.config(state=tk.DISABLED)
                except tk.TclError:
                    pass
            self._thinking_marker = None

    def _animate_thinking(self) -> None:
        """Cycle dots on the thinking indicator: . → .. → ... → ."""
        if self._thinking_marker is None:
            return
        dots = getattr(self, "_thinking_dots", 3)
        dots = (dots % 3) + 1
        self._thinking_dots = dots
        label = "Jarvis is thinking" + "." * dots + "\n"
        try:
            self.output.config(state=tk.NORMAL)
            self.output.delete(self._thinking_marker, tk.END)
            self.output.insert(tk.END, label, "thinking")
            self.output.see(tk.END)
            self.output.config(state=tk.DISABLED)
        except tk.TclError:
            return
        self._thinking_after_id = self.after(400, self._animate_thinking)
```

**Step 4: Add `_thinking_after_id` and `_thinking_dots` instance variables**

Near line 714 (after `_thinking_marker`), add:

```python
        self._thinking_after_id: str | None = None  # after() id for dot animation
        self._thinking_dots: int = 3
```

**Step 5: Wire thinking indicator into `_send_command_async()`**

In `_send_command_async()` (line 1746), after `self._set_state("processing")` (line 1756), add:

```python
        self._show_thinking()
        # Disable input while processing
        self.command_text.config(state=tk.DISABLED)
```

Then in the `worker()` function, BEFORE every `self._log_async(...)` call and BEFORE every `self._set_error_briefly_async()` call, add a `self.after(0, self._hide_thinking)` call. Specifically, wrap the entire worker try/finally:

Replace the worker function body with a try/finally that calls `_hide_thinking` and re-enables input:

```python
        def worker() -> None:
            try:
                payload = {
                    "text": text,
                    "execute": execute,
                    "approve_privileged": approve_privileged,
                    "speak": speak,
                    "master_password": cfg.master_password,
                }
                data = _http_json(cfg, "/command", method="POST", payload=payload)
                self.after(0, self._hide_thinking)
                # Parse clean response from stdout_tail
                lines = data.get("stdout_tail", [])
                response_text = ""
                if isinstance(lines, list):
                    for line in lines:
                        s = str(line)
                        if s.startswith("response="):
                            response_text = s[len("response="):]
                            break
                intent = str(data.get("intent", "unknown"))
                ok = bool(data.get("ok", False))
                if response_text:
                    self._log_async(response_text, role="jarvis")
                else:
                    self._log_async(f"[{intent}] ok={ok}", role="jarvis")
                    if isinstance(lines, list) and lines:
                        self._log_async(" | ".join(str(x) for x in lines[-6:]), role="jarvis")
                if not ok:
                    self._set_error_briefly_async()
                else:
                    self._set_state_async("idle")
            except HTTPError as exc:
                self.after(0, self._hide_thinking)
                self._log_async(f"Command failed: {_http_error_details(exc)}", role="error")
                self._set_error_briefly_async()
            except URLError:
                self.after(0, self._hide_thinking)
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
                self._set_error_briefly_async()
            except (RuntimeError, TimeoutError) as exc:
                self.after(0, self._hide_thinking)
                self._log_async(f"Command failed: {exc}", role="error")
                self._set_error_briefly_async()
            except Exception as exc:  # noqa: BLE001
                self.after(0, self._hide_thinking)
                self._log_async(f"Command failed: {exc}", role="error")
                self._set_error_briefly_async()
            finally:
                # Re-enable input
                try:
                    self.after(0, lambda: self.command_text.config(state=tk.NORMAL))
                except Exception:
                    pass
```

**Step 6: Run tests**

Run: `python -m pytest engine/tests/test_main.py -x -q`
Expected: All pass (widget changes are UI-only, tests mock HTTP)

**Step 7: Commit**

```bash
git add engine/src/jarvis_engine/desktop_widget.py
git commit -m "feat: typing indicator with animated dots during command processing"
```

---

### Task 2: Parse Clean Response from stdout_tail

This was already handled in Task 1 Step 5. The widget now parses `response=...` from `stdout_tail` and shows clean conversational text instead of raw intent/ok lines. Verify the "remember" and "brain_context" paths also print `response=`:

**Files:**
- Modify: `engine/src/jarvis_engine/main.py:3609-3614` (add response= print for remember)
- Modify: `engine/src/jarvis_engine/main.py:3531-3569` (verify brain_context prints response=)

**Step 1: Check and add `response=` prints for memory commands**

In the "remember" handler (around line 3609), after `rc = cmd_ingest(...)`, add:

```python
        if rc == 0:
            print(f"response=Got it, I'll remember that.")
        else:
            print(f"response=Sorry, I couldn't save that to memory.")
```

In the "memory search" handler (around line 3531-3569), verify `cmd_brain_context` prints `response=`. Check if it does by reading its implementation.

**Step 2: Add `response=` print for knowledge status and system status**

After `rc = cmd_brain_status(as_json=False)` (line 3627), add:
```python
        # brain_status already prints to stdout; widget will parse stdout_tail fallback
```

No change needed — these print directly to stdout and the widget's fallback parsing handles them.

**Step 3: Run tests**

Run: `python -m pytest engine/tests/ -x -q -k "voice_run or brain"`
Expected: All pass

**Step 4: Commit**

```bash
git add engine/src/jarvis_engine/main.py
git commit -m "feat: add response= output for memory commands for clean widget display"
```

---

### Task 3: Help Button + Tooltip System

**Files:**
- Modify: `engine/src/jarvis_engine/desktop_widget.py:1050-1072` (add ? button to header)
- Modify: `engine/src/jarvis_engine/desktop_widget.py` (add help overlay + tooltip class)

**Step 1: Add Tooltip helper class**

At the top of the file (after imports, before the `JarvisWidget` class), add:

```python
class _Tooltip:
    """Hover tooltip for tkinter widgets."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 300) -> None:
        self._widget = widget
        self._text = text
        self._delay = delay
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self, _event: Any = None) -> None:
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self) -> None:
        if self._tip:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        label = tk.Label(
            tw, text=self._text, justify=tk.LEFT,
            background="#1e293b", foreground="#e2e8f0",
            relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 9), padx=6, pady=3,
        )
        label.pack()

    def _hide(self) -> None:
        if self._tip:
            self._tip.destroy()
            self._tip = None
```

**Step 2: Add `?` Help button in the header**

In `_build_ui()`, after the Exit button (line 1072), BEFORE the status_row, add:

```python
        tk.Button(
            top,
            text="?",
            bg="#1a3050",
            fg="#a5b4fc",
            activebackground="#2a4060",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 11, "bold"),
            command=self._show_help,
            cursor="hand2",
            width=2,
        ).pack(side=tk.RIGHT, padx=(0, 6))
```

**Step 3: Add `_show_help()` method**

After `_hide_thinking()`, add:

```python
    def _show_help(self) -> None:
        """Show help overlay with commands and tips."""
        help_win = tk.Toplevel(self)
        help_win.title("Jarvis Help")
        help_win.geometry("420x480")
        help_win.configure(bg="#0a1628")
        help_win.attributes("-topmost", True)
        help_win.resizable(False, False)

        tk.Label(
            help_win, text="Jarvis Help", bg="#0a1628", fg="#e2e8f0",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(16, 8))

        sections = [
            ("Talking to Jarvis", [
                "Type any question or command in the text box",
                "Press Enter or click Send",
                "Click Voice Dictate or say 'Jarvis' (wake word)",
                "Ctrl+Enter also sends the command",
            ]),
            ("Teaching Jarvis", [
                '"Remember that [fact]" — saves to memory',
                '"What do you know about [topic]?" — queries memory',
                '"Forget [topic]" — removes from knowledge base',
                "Jarvis auto-learns from every conversation",
            ]),
            ("Quick Commands", [
                '"Knowledge status" — brain health report',
                '"System status" — service health check',
                '"Mission status" — active learning missions',
                '"Pause/Resume Jarvis" — control the daemon',
            ]),
            ("Keyboard Shortcuts", [
                "Enter — Send command",
                "Ctrl+Enter — Send command (alternative)",
                "Escape — Close this help window",
            ]),
        ]

        text = tk.Text(
            help_win, wrap=tk.WORD, bg="#0a1628", fg="#cbd5e1",
            font=("Segoe UI", 10), relief=tk.FLAT, padx=16, pady=8,
            state=tk.DISABLED, highlightthickness=0,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        text.tag_configure("heading", foreground="#6ee7b7", font=("Segoe UI", 11, "bold"))
        text.tag_configure("item", foreground="#cbd5e1", font=("Segoe UI", 10))
        text.config(state=tk.NORMAL)
        for heading, items in sections:
            text.insert(tk.END, f"\n{heading}\n", "heading")
            for item in items:
                text.insert(tk.END, f"  {item}\n", "item")
        text.config(state=tk.DISABLED)

        help_win.bind("<Escape>", lambda _: help_win.destroy())
        help_win.focus_set()
```

**Step 4: Add tooltips to key widgets**

At the end of `_build_ui()` (after line 1276), add:

```python
        # Tooltips on key controls
        _Tooltip(self.command_text, "Type a command or question")
```

And after the Voice Dictate / Send buttons are created (line 1183-1184), store references and add tooltips. Replace lines 1183-1184 with:

```python
        self._voice_btn = self._btn(row, "Voice Dictate", self._dictate_async, self.ACCENT_2)
        self._voice_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._send_btn = self._btn(row, "Send", self._send_command_async, self.ACCENT)
        self._send_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(self._voice_btn, "Click or say 'Jarvis' to dictate")
        _Tooltip(self._send_btn, "Send command (Enter)")
```

**Step 5: Run tests**

Run: `python -m pytest engine/tests/test_main.py -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add engine/src/jarvis_engine/desktop_widget.py
git commit -m "feat: help button with overlay + hover tooltips on widget controls"
```

---

### Task 4: Cold Start Optimization — Pre-warm Bus in Mobile API

**Files:**
- Modify: `engine/src/jarvis_engine/mobile_api.py:1556-1560` (pre-warm before serve_forever)
- Modify: `engine/src/jarvis_engine/app.py:357-498` (lazy-load 4 subsystems)

**Step 1: Pre-warm CommandBus in `run_mobile_server()`**

In `run_mobile_server()`, BEFORE `server.serve_forever()` (find this line — approximately line 1626), add:

```python
    # Pre-warm the CommandBus so the first user request doesn't pay cold start cost
    def _prewarm():
        try:
            import jarvis_engine.main as main_mod
            original = main_mod.repo_root
            main_mod.repo_root = lambda: repo_root  # type: ignore[assignment]
            try:
                main_mod._get_bus()
            finally:
                main_mod.repo_root = original  # type: ignore[assignment]
            logger.info("CommandBus pre-warmed successfully")
        except Exception as exc:
            logger.warning("CommandBus pre-warm failed (will warm on first request): %s", exc)
    import threading as _threading
    _threading.Thread(target=_prewarm, daemon=True, name="bus-prewarm").start()
```

**Step 2: Find exact location of `serve_forever()` in mobile_api.py**

Read lines around 1620-1650 to find the right insertion point.

**Step 3: Lazy-load Harvesting subsystem in `create_app()`**

In `engine/src/jarvis_engine/app.py`, replace the Harvesting block (lines 447-479) with a lazy-loading wrapper. Instead of eagerly creating all providers, register a lazy handler:

```python
    # -- Harvesting (lazy-loaded on first use) --
    _harvester_ref: list = [None]  # mutable container for closure

    def _get_harvester():
        if _harvester_ref[0] is not None:
            return _harvester_ref[0]
        try:
            from jarvis_engine.harvesting.budget import BudgetManager
            from jarvis_engine.harvesting.providers import (
                GeminiProvider, KimiNvidiaProvider, KimiProvider, MiniMaxProvider,
            )
            from jarvis_engine.harvesting.harvester import KnowledgeHarvester
            budget_manager = BudgetManager(db_path) if db_path.exists() else None
            all_providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]
            available_providers = [p for p in all_providers if p.is_available]
            _harvester_ref[0] = KnowledgeHarvester(
                providers=available_providers, pipeline=pipeline,
                cost_tracker=cost_tracker, budget_manager=budget_manager,
            )
            return _harvester_ref[0]
        except Exception as exc:
            logger.warning("Failed to initialize Harvesting: %s", exc)
            return None

    # Register handlers that lazily initialize on first call
    bus.register(HarvestTopicCommand, HarvestHandler(harvester_factory=_get_harvester).handle)
    bus.register(IngestSessionCommand, IngestSessionHandler(pipeline=pipeline).handle)
    bus.register(HarvestBudgetCommand, HarvestBudgetHandler(budget_factory=lambda: None).handle)
```

**IMPORTANT:** This approach requires changing HarvestHandler to accept a factory. Since this is a deeper refactor that could break the handler interface, a simpler approach is better: just move the imports but keep eager instantiation, and defer only the slow parts. Actually, the simplest cold-start win is the pre-warm in Step 1 — it runs `create_app()` in a background thread so the mobile API is accepting connections immediately while the bus warms up. The user sees fast `/health` response while the bus initializes.

**Revised Step 3: Keep `create_app()` as-is, rely on background pre-warm**

The pre-warm thread in Step 1 already handles cold start. `create_app()` is complex with many interdependencies — lazy-loading individual subsystems risks breaking handler registration. The pre-warm approach gives us the win (first request hits warm cache) without refactoring app.py.

**Step 4: Add background embedding model warm-up**

In `create_app()`, after line 516 (`bus._gateway = gateway`), add:

```python
    # Warm embedding model in background (first embed call loads the 300MB model)
    if embed_service is not None:
        def _warm_embeddings():
            try:
                embed_service.embed("warmup", prefix="search_document")
                logger.info("Embedding model warmed up")
            except Exception as exc:
                logger.debug("Embedding warm-up failed (will load on first use): %s", exc)
        import threading as _threading
        _threading.Thread(target=_warm_embeddings, daemon=True, name="embed-warmup").start()
```

**Step 5: Run tests**

Run: `python -m pytest engine/tests/ -x -q`
Expected: All pass (pre-warm is background thread, doesn't affect test behavior)

**Step 6: Commit**

```bash
git add engine/src/jarvis_engine/mobile_api.py engine/src/jarvis_engine/app.py
git commit -m "perf: pre-warm CommandBus at mobile API startup + background embedding warm-up"
```

---

### Task 5: Forget Command

**Files:**
- Modify: `engine/src/jarvis_engine/main.py:3570` (add forget handler before remember handler)
- Modify: `engine/src/jarvis_engine/knowledge/graph.py` (add `retract_facts()` method)
- Test: `engine/tests/test_main.py` (add test for forget command)

**Step 1: Add `retract_facts()` to KnowledgeGraph**

In `engine/src/jarvis_engine/knowledge/graph.py`, after `query_relevant_facts()` (line 429), add:

```python
    def retract_facts(self, keywords: list[str]) -> int:
        """Soft-retract KG facts matching keywords by setting confidence to 0.

        Returns the number of facts retracted.
        """
        if not keywords:
            return 0
        clauses = []
        params: list[object] = []
        for kw in keywords[:20]:
            sanitized = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("label LIKE ? ESCAPE '\\'")
            params.append(f"%{sanitized}%")
        sql = (
            "UPDATE kg_nodes SET confidence = 0.0, updated_at = ? "
            "WHERE (" + " OR ".join(clauses) + ") AND confidence > 0 AND locked = 0"
        )
        from datetime import datetime, UTC
        params.insert(0, datetime.now(UTC).isoformat())
        # Move timestamp to end for correct param order
        # Actually: params order is [timestamp, kw1, kw2, ...] but SQL has WHERE first then SET
        # Fix: build params correctly
        now = datetime.now(UTC).isoformat()
        with self._write_lock:
            with self._db_lock:
                cur = self._db.execute(
                    "UPDATE kg_nodes SET confidence = 0.0, updated_at = ? "
                    "WHERE (" + " OR ".join(clauses) + ") AND confidence > 0 AND locked = 0",
                    [now] + params,
                )
                self._db.commit()
                return cur.rowcount
```

Wait — the params list already has the LIKE patterns. Let me write it more carefully:

```python
    def retract_facts(self, keywords: list[str]) -> int:
        """Soft-retract KG facts matching keywords by setting confidence to 0.

        Does not retract locked facts. Returns the number of facts retracted.
        """
        if not keywords:
            return 0
        clauses = []
        like_params: list[str] = []
        for kw in keywords[:20]:
            sanitized = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("label LIKE ? ESCAPE '\\'")
            like_params.append(f"%{sanitized}%")
        from datetime import datetime, UTC
        now = datetime.now(UTC).isoformat()
        sql = (
            "UPDATE kg_nodes SET confidence = 0.0, updated_at = ? "
            "WHERE (" + " OR ".join(clauses) + ") AND confidence > 0 AND locked = 0"
        )
        all_params: list[object] = [now] + like_params
        with self._write_lock:
            with self._db_lock:
                cur = self._db.execute(sql, all_params)
                self._db.commit()
                return cur.rowcount
```

**Step 2: Add "forget" keyword handler in `_cmd_voice_run_impl()`**

In `engine/src/jarvis_engine/main.py`, BEFORE the "remember" handler block (line 3570), add:

```python
    # --- Forget / unlearn ---
    elif any(
        k in lowered
        for k in [
            "forget about",
            "forget that",
            "forget everything about",
            "unlearn",
            "stop remembering",
            "delete memory of",
            "remove from memory",
        ]
    ):
        intent = "memory_forget"
        _forget_triggers = [
            "forget everything about",
            "forget about",
            "forget that",
            "delete memory of",
            "remove from memory",
            "stop remembering",
            "unlearn",
        ]
        topic = text
        for trigger in _forget_triggers:
            if trigger in lowered:
                idx = lowered.index(trigger) + len(trigger)
                topic = text[idx:].strip().rstrip(".").strip()
                break
        if not topic:
            print("response=What should I forget? Try 'Forget about [topic]'.")
            rc = 0
        else:
            bus = _get_bus()
            kg = getattr(bus, "_kg", None)
            if kg is not None:
                keywords = [w for w in topic.split() if len(w) > 2]
                if not keywords:
                    keywords = [topic]
                count = kg.retract_facts(keywords)
                print(f"response=Done. I've forgotten {count} fact(s) about '{topic}'.")
                rc = 0
            else:
                print("response=Knowledge graph is not available right now.")
                rc = 1
```

**Step 3: Write test for forget command**

Add to the test file (or verify existing test structure handles it). Since `_cmd_voice_run_impl` is called through `cmd_voice_run` which dispatches `VoiceRunCommand`, we need a test that exercises the keyword matching. This is best tested as an integration test through the voice-run path.

**Step 4: Run tests**

Run: `python -m pytest engine/tests/ -x -q`
Expected: All pass

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/knowledge/graph.py engine/src/jarvis_engine/main.py
git commit -m "feat: forget command — soft-retract KG facts by keyword"
```

---

### Task 6: Onboarding Welcome Message

**Files:**
- Modify: `engine/src/jarvis_engine/desktop_widget.py:1664-1693` (add welcome after bootstrap)

**Step 1: Add welcome message flag**

In `__init__` near line 714, add:

```python
        self._welcome_shown: bool = False
```

**Step 2: Add `_show_welcome()` method**

After `_show_help()`, add:

```python
    def _show_welcome(self) -> None:
        """Show one-time welcome message in chat."""
        if self._welcome_shown:
            return
        self._welcome_shown = True
        self._log(
            "Hi! I'm Jarvis. Ask me anything, teach me with "
            "'Remember that...', or click ? for help.",
            role="jarvis",
        )
```

**Step 3: Wire welcome into bootstrap success**

In `_bootstrap_session_async()`, in the worker success path (line 1682), after the "Bootstrap complete" log, add:

```python
                self.after(0, self._show_welcome)
```

**Step 4: Run tests**

Run: `python -m pytest engine/tests/test_main.py -x -q`
Expected: All pass

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/desktop_widget.py
git commit -m "feat: onboarding welcome message on first widget connection"
```

---

### Task 7: "Learned" Indicator After Memory Commands

**Files:**
- Modify: `engine/src/jarvis_engine/desktop_widget.py` (add learned indicator)

**Step 1: Add "learned" chat tag**

In `_configure_chat_tags()`, after the "thinking" tag, add:

```python
        self.output.tag_configure(
            "learned",
            foreground="#34d399",
            font=("Consolas", 9, "italic"),
            lmargin1=8,
            lmargin2=8,
            spacing1=1,
            spacing3=1,
        )
```

**Step 2: Add `_show_learned_indicator()` method**

```python
    def _show_learned_indicator(self) -> None:
        """Show a brief 'Learned' indicator that fades after 2s."""
        self.output.config(state=tk.NORMAL)
        marker = self.output.index(tk.END)
        self.output.insert(tk.END, "  Learned\n", "learned")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

        def _remove():
            try:
                self.output.config(state=tk.NORMAL)
                self.output.delete(marker, f"{marker}+1l")
                self.output.config(state=tk.DISABLED)
            except tk.TclError:
                pass

        self.after(2000, _remove)
```

**Step 3: Wire into response parsing**

In `_send_command_async()` worker, after displaying the response, check if the intent was a learning-related one:

```python
                if intent in ("memory_ingest", "memory_forget", "llm_conversation"):
                    self.after(0, self._show_learned_indicator)
```

**Step 4: Run tests**

Run: `python -m pytest engine/tests/test_main.py -x -q`
Expected: All pass

**Step 5: Commit**

```bash
git add engine/src/jarvis_engine/desktop_widget.py
git commit -m "feat: subtle 'Learned' indicator after memory-related commands"
```

---

### Task 8: Final Verification and Push

**Step 1: Run full test suite**

Run: `python -m pytest engine/tests/ -x -q`
Expected: 3770+ pass, <=9 skip, 0 fail

**Step 2: Verify no regressions in widget startup**

Run: `python -c "from jarvis_engine.desktop_widget import JarvisWidget; print('import ok')"`
Expected: "import ok"

**Step 3: Push all commits**

```bash
git push origin main
```

**Step 4: Update MEMORY.md with new patterns**

Add notes about typing indicator, help system, cold start pre-warm, forget command.
