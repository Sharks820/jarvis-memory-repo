"""Conversation and chat window logic for the Jarvis desktop widget.

Contains the ``ConversationMixin`` class that provides the conversation
display, pop-out window, thinking indicator, chat logging, and help
overlay.  Extracted from ``desktop_widget.py`` to improve separation
of concerns.

Classes and methods in this module are intended to be mixed into
``JarvisDesktopWidget`` via multiple inheritance; they should NOT be
used standalone.
"""

from __future__ import annotations

import logging
import time
import tkinter as tk
from typing import Any, ClassVar

from jarvis_engine.widget_helpers import _http_json

logger = logging.getLogger(__name__)


class ConversationMixin:
    """Mixin providing conversation display, chat logging, and pop-out window.

    Expects the host class to supply the following attributes:
      - ``self.BG``, ``self.PANEL``, ``self.EDGE``, ``self.TEXT``,
        ``self.MUTED``, ``self.ACCENT``
      - ``self.output`` (tk.Text), ``self._chat_frame``
      - ``self.stop_event``, ``self._widget_state``
      - ``self.command_text``
      - ``self.after()`` (tkinter scheduling)
    """

    # Chat tag style definitions: (name, {config kwargs})
    _CHAT_TAG_SPECS: ClassVar[list[tuple[str, dict]]] = [
        (
            "user",
            {
                "background": "#0c2d5e",
                "foreground": "#b8d4ff",
                "font": ("Consolas", 11, "bold"),
                "lmargin1": 40,
                "lmargin2": 40,
                "rmargin": 8,
                "spacing1": 4,
                "spacing3": 4,
            },
        ),
        (
            "jarvis",
            {
                "background": "#0d1e1e",
                "foreground": "#a8e6cf",
                "font": ("Consolas", 11),
                "lmargin1": 8,
                "lmargin2": 8,
                "rmargin": 40,
                "spacing1": 4,
                "spacing3": 4,
            },
        ),
        (
            "system",
            {
                "foreground": "#7a9abe",
                "font": ("Consolas", 10),
                "lmargin1": 8,
                "lmargin2": 8,
                "spacing1": 2,
                "spacing3": 2,
            },
        ),
        (
            "error",
            {
                "background": "#2a0a0a",
                "foreground": "#ff6b6b",
                "font": ("Consolas", 11, "bold"),
                "lmargin1": 8,
                "lmargin2": 8,
                "spacing1": 4,
                "spacing3": 4,
            },
        ),
        (
            "separator",
            {
                "foreground": "#1e3250",
                "font": ("Consolas", 6),
                "justify": "center",
                "spacing1": 2,
                "spacing3": 2,
            },
        ),
        (
            "timestamp",
            {
                "foreground": "#3a5a7e",
                "font": ("Consolas", 8),
                "justify": "center",
                "spacing1": 6,
                "spacing3": 2,
            },
        ),
        (
            "thinking",
            {
                "foreground": "#ff9f43",
                "font": ("Consolas", 11, "italic"),
                "lmargin1": 8,
                "lmargin2": 8,
                "rmargin": 40,
                "spacing1": 4,
                "spacing3": 4,
            },
        ),
        (
            "learned",
            {
                "foreground": "#34d399",
                "font": ("Consolas", 9, "italic"),
                "lmargin1": 8,
                "lmargin2": 8,
                "spacing1": 1,
                "spacing3": 1,
            },
        ),
    ]

    # ------------------------------------------------------------------
    # Chat area build
    # ------------------------------------------------------------------

    def _build_chat_header(self, body: tk.Frame) -> None:
        """Build the conversation header with Clear, End Conversation, and Pop Out buttons."""
        output_header = tk.Frame(body, bg=self.PANEL)
        output_header.pack(fill=tk.X, padx=10, pady=(10, 0))
        tk.Label(
            output_header,
            text="\U0001f4ac  Conversation",
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT)
        tk.Button(
            output_header,
            text="Clear",
            bg="#1a2742",
            fg=self.MUTED,
            activebackground="#2a3752",
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            command=self._clear_history,
            cursor="hand2",
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            output_header,
            text="\u23f9 End Conversation",
            bg="#7a2f2f",
            fg="#fca5a5",
            activebackground="#a03030",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            command=self._end_conversation,
            cursor="hand2",
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            output_header,
            text="\u2197 Pop Out",
            bg="#2a3f5f",
            fg=self.TEXT,
            activebackground="#3a5070",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            command=self._pop_out_conversation,
            cursor="hand2",
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT)

    def _build_thinking_indicator(self, body: tk.Frame) -> None:
        """Build the thinking indicator label (hidden by default, shown during processing)."""
        self._thinking_frame = tk.Frame(body, bg="#1a1500")
        self._thinking_label_widget = tk.Label(
            self._thinking_frame,
            text="",
            bg="#1a1500",
            fg="#ff9f43",
            font=("Consolas", 11, "italic"),
            anchor="w",
            padx=8,
            pady=4,
        )
        self._thinking_label_widget.pack(fill=tk.X)

    def _build_chat_area(self, body: tk.Frame) -> None:
        """Build the conversation output area with header, thinking indicator, and chat text."""
        self._build_chat_header(body)
        self._build_thinking_indicator(body)

        self._chat_frame = tk.Frame(body, bg="#081127")
        self._chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self.output = tk.Text(
            self._chat_frame,
            wrap=tk.WORD,
            bg="#081127",
            fg="#d6e4ff",
            insertbackground="#d6e4ff",
            relief=tk.FLAT,
            highlightbackground="#3a5a8a",
            highlightthickness=2,
            font=("Consolas", 11),
            state=tk.DISABLED,
        )
        output_scroll = tk.Scrollbar(
            self._chat_frame,
            command=self.output.yview,
            bg="#0a1a3a",
            troughcolor="#0d1628",
            activebackground="#1e3250",
        )
        self.output.config(yscrollcommand=output_scroll.set)
        output_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.pack(fill=tk.BOTH, expand=True)
        self._configure_chat_tags()

    def _configure_chat_tags(self) -> None:
        """Set up tag-based visual styles for the chat-style conversation display."""
        for name, kwargs in self._CHAT_TAG_SPECS:
            self.output.tag_configure(name, **kwargs)

    # ------------------------------------------------------------------
    # Chat history management
    # ------------------------------------------------------------------

    def _clear_history(self) -> None:
        """Clear all text from the conversation display."""
        self.output.config(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.config(state=tk.DISABLED)

    def _end_conversation(self) -> None:
        """End the conversation session, clear server-side history, and reset."""
        # Reset local UI state
        self._cancel_processing_timeout()
        self._hide_thinking()
        self._set_state("idle")
        self.command_text.config(state=tk.NORMAL)
        self._log("Conversation ended. Starting fresh.", role="system")
        # Clear server-side conversation history
        cfg = self._current_cfg()

        def worker() -> None:
            try:
                _http_json(cfg, "/conversation/clear", method="POST", payload={})
            except (OSError, ValueError, KeyError, TimeoutError) as exc:
                logger.debug("Best-effort conversation clear failed: %s", exc)

        self._thread(worker)

    # ------------------------------------------------------------------
    # Pop-out conversation window
    # ------------------------------------------------------------------

    def _pop_out_conversation(self) -> None:
        """Open conversation in a separate resizable window with command input."""
        if hasattr(self, "_popout_win") and self._popout_win is not None:
            try:
                self._popout_win.lift()
                self._popout_win.focus_force()
                return
            except tk.TclError:
                logger.debug("Popout window was destroyed; will recreate")
                self._popout_win = None

        win = self._setup_conversation_window()
        self._bind_conversation_events(win)

    def _setup_conversation_window(self) -> tk.Toplevel:
        """Create the pop-out conversation window with display and input areas."""
        win = tk.Toplevel(self)
        win.title("Jarvis \u2014 Conversation")
        win.geometry("750x600")
        win.minsize(500, 400)
        win.configure(bg=self.BG)
        win.attributes("-topmost", True)
        self._popout_win = win

        # --- Thinking indicator for popout ---
        self._popout_thinking_frame = tk.Frame(win, bg="#1a1500")
        self._popout_thinking_label = tk.Label(
            self._popout_thinking_frame,
            text="",
            bg="#1a1500",
            fg="#ff9f43",
            font=("Consolas", 12, "italic"),
            anchor="w",
            padx=8,
            pady=4,
        )
        self._popout_thinking_label.pack(fill=tk.X)
        # Not packed yet — shown via _show_thinking() when processing

        popout_text = self._build_popout_display(win)
        self._popout_text = popout_text
        popout_cmd, send_btn = self._build_popout_input(win)

        # Store references for event binding
        win._popout_cmd = popout_cmd  # type: ignore[attr-defined]
        win._send_btn = send_btn  # type: ignore[attr-defined]
        return win

    def _build_popout_display(self, win: tk.Toplevel) -> tk.Text:
        """Build the conversation display area for the pop-out window and copy existing content."""
        chat_frame = tk.Frame(win, bg=self.BG)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        popout_text = tk.Text(
            chat_frame,
            wrap=tk.WORD,
            bg="#081127",
            fg="#d6e4ff",
            insertbackground="#d6e4ff",
            relief=tk.FLAT,
            highlightbackground="#3a5a8a",
            highlightthickness=2,
            font=("Consolas", 12),
            state=tk.DISABLED,
        )
        scrollbar = tk.Scrollbar(
            chat_frame,
            command=popout_text.yview,
            bg="#0a1a3a",
            troughcolor="#0d1628",
            activebackground="#1e3250",
        )
        popout_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        popout_text.pack(fill=tk.BOTH, expand=True)

        # Configure the same chat tags on the pop-out widget
        for tag in (
            "user",
            "jarvis",
            "system",
            "error",
            "separator",
            "timestamp",
            "thinking",
            "learned",
        ):
            tag_opts = self.output.tag_configure(tag)
            if tag_opts:
                resolved = {k: v[-1] for k, v in tag_opts.items() if v and v[-1]}
                if resolved:
                    popout_text.tag_configure(tag, **resolved)

        # Copy content preserving tags -- use tag_ranges() for O(n) bulk copy
        popout_text.config(state=tk.NORMAL)
        full_text = self.output.get("1.0", tk.END)
        if full_text.strip():
            popout_text.insert(tk.END, full_text)
            for tag in (
                "user",
                "jarvis",
                "system",
                "error",
                "separator",
                "timestamp",
                "thinking",
                "learned",
            ):
                try:
                    ranges = self.output.tag_ranges(tag)
                    for i in range(0, len(ranges), 2):
                        popout_text.tag_add(tag, str(ranges[i]), str(ranges[i + 1]))
                except tk.TclError:
                    logger.debug("Failed to copy tag %r to popout text widget", tag)
        popout_text.config(state=tk.DISABLED)
        return popout_text

    def _build_popout_input(self, win: tk.Toplevel) -> tuple[tk.Text, tk.Button]:
        """Build the command input area for the pop-out window. Returns (cmd_text, send_btn)."""
        input_frame = tk.Frame(
            win, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1
        )
        input_frame.pack(fill=tk.X, padx=8, pady=8)

        # Accent bar at top of input area
        tk.Frame(input_frame, bg=self.ACCENT, height=2).pack(fill=tk.X)

        input_inner = tk.Frame(input_frame, bg=self.PANEL)
        input_inner.pack(fill=tk.X, padx=8, pady=8)

        popout_cmd = tk.Text(
            input_inner,
            height=3,
            wrap=tk.WORD,
            bg="#081127",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
            font=("Consolas", 12),
        )
        popout_cmd.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 8))

        btn_frame = tk.Frame(input_inner, bg=self.PANEL)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y)

        send_btn = tk.Button(
            btn_frame,
            text="\u25b6 Send",
            bg=self.ACCENT,
            fg="#000000",
            activebackground="#0ea5a0",
            activeforeground="#000000",
            relief=tk.FLAT,
            font=("Segoe UI", 11, "bold"),
            cursor="hand2",
            padx=16,
            pady=6,
        )
        send_btn.pack(fill=tk.BOTH, expand=True)
        return popout_cmd, send_btn

    def _bind_conversation_events(self, win: tk.Toplevel) -> None:
        """Wire up keyboard and button events for the pop-out conversation window."""
        popout_cmd = win._popout_cmd  # type: ignore[attr-defined]
        send_btn = win._send_btn  # type: ignore[attr-defined]

        def _popout_send(event: tk.Event[Any] | None = None) -> str | None:
            text = popout_cmd.get("1.0", tk.END).strip()
            if not text:
                return None
            popout_cmd.delete("1.0", tk.END)
            # Mirror to main command box and send
            self._set_command_text(text)
            self._send_command_async()
            return "break"

        def _popout_key(event: tk.Event[Any]) -> str | None:
            if (
                event.keysym == "Return" and not event.state & 0x1
            ):  # Enter without Shift
                _popout_send()
                return "break"
            return None

        popout_cmd.bind("<Key>", _popout_key)
        send_btn.config(command=_popout_send)
        popout_cmd.focus_set()

        def _on_close() -> None:
            self._popout_win = None
            self._popout_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    # ------------------------------------------------------------------
    # Chat logging
    # ------------------------------------------------------------------

    def _log(self, message: str, role: str = "system") -> None:
        stamp = time.strftime("%H:%M:%S")
        self.output.config(state=tk.NORMAL)

        # Build the display line based on role
        if role == "user":
            prefix = "You"
            display = f"{prefix}: {message}\n"
        elif role == "jarvis":
            prefix = "Jarvis"
            display = f"{prefix}: {message}\n"
        elif role == "error":
            display = f"[{stamp}] ERROR: {message}\n"
        else:
            # system (default)
            display = f"[{stamp}] {message}\n"

        # Insert separator + timestamp before new exchanges (user messages)
        if role == "user":
            sep_line = "\u2500" * 48 + "\n"
            self.output.insert(tk.END, sep_line, "separator")
            self.output.insert(tk.END, f"  {stamp}  \n", "timestamp")

        # Insert the message with the appropriate tag
        tag = role if role in ("user", "jarvis", "system", "error") else "system"
        self.output.insert(tk.END, display, tag)

        self.output.see(tk.END)

        # Limit output widget to 500 lines to prevent unbounded memory growth
        line_count = int(self.output.index("end-1c").split(".")[0])
        if line_count > 500:
            self.output.delete("1.0", f"{line_count - 500}.0")

        self.output.config(state=tk.DISABLED)

        # Mirror to pop-out conversation window if open
        popout = getattr(self, "_popout_text", None)
        if popout is not None:
            try:
                popout.config(state=tk.NORMAL)
                if role == "user":
                    sep_line = "\u2500" * 48 + "\n"
                    popout.insert(tk.END, sep_line, "separator")
                    popout.insert(tk.END, f"  {stamp}  \n", "timestamp")
                popout.insert(tk.END, display, tag)
                popout.see(tk.END)
                popout.config(state=tk.DISABLED)
            except tk.TclError:
                logger.debug("Popout text widget was destroyed; clearing reference")
                self._popout_text = None

    def _log_async(self, message: str, role: str = "system") -> None:
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._log, message, role)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("_log_async failed (widget may be destroyed)")

    # ------------------------------------------------------------------
    # Thinking indicator
    # ------------------------------------------------------------------

    def _show_thinking(self) -> None:
        """Show animated 'Jarvis is thinking...' indicator as a Label widget.

        Uses a dedicated Label (not text-mark manipulation) to avoid loading
        bar spam from mark-based insert/delete race conditions.
        """
        import time as _time

        self._thinking_start_time = _time.time()
        self._thinking_dots = 3
        self._thinking_label_widget.config(text="\u23f3 Jarvis is thinking...  (0s)")
        self._thinking_frame.pack(
            fill=tk.X, padx=10, pady=(2, 0), before=self._chat_frame
        )
        # Also show in popout if open
        popout_lbl = getattr(self, "_popout_thinking_label", None)
        if popout_lbl is not None:
            try:
                popout_frame = getattr(self, "_popout_thinking_frame", None)
                if popout_frame is not None:
                    popout_frame.pack(fill=tk.X, padx=8, pady=(2, 0))
                popout_lbl.config(text="\u23f3 Jarvis is thinking...  (0s)")
            except tk.TclError:
                logger.debug("Failed to show thinking indicator in popout window")
        self._animate_thinking()

    def _hide_thinking(self) -> None:
        """Hide the thinking indicator Label."""
        if self._thinking_after_id is not None:
            try:
                self.after_cancel(self._thinking_after_id)
            except (tk.TclError, RuntimeError):  # Widget may be destroyed
                logger.debug("Failed to cancel thinking animation timer")
            self._thinking_after_id = None
        try:
            self._thinking_frame.pack_forget()
        except tk.TclError:
            logger.debug("Failed to hide thinking frame")
        # Hide popout thinking label
        popout_frame = getattr(self, "_popout_thinking_frame", None)
        if popout_frame is not None:
            try:
                popout_frame.pack_forget()
            except tk.TclError:
                logger.debug("Failed to hide popout thinking frame")

    def _animate_thinking(self) -> None:
        """Update thinking indicator Label with elapsed time and progress bar."""
        import time as _time

        if self._widget_state != "processing":
            return
        self._thinking_dots = (self._thinking_dots % 3) + 1
        elapsed = int(_time.time() - self._thinking_start_time)
        dots = "." * self._thinking_dots
        # Build progress bar: fills over 30 seconds
        bar_len = 20
        filled = min(bar_len, int(elapsed * bar_len / 30))
        bar = "\u2593" * filled + "\u2591" * (bar_len - filled)
        label = f"\u23f3 Jarvis is thinking{dots}  ({elapsed}s)  [{bar}]"
        try:
            self._thinking_label_widget.config(text=label)
        except tk.TclError:
            logger.debug("Thinking label widget destroyed; stopping animation")
            return
        # Mirror animation to pop-out thinking label
        popout_lbl = getattr(self, "_popout_thinking_label", None)
        if popout_lbl is not None:
            try:
                popout_lbl.config(text=label)
            except tk.TclError:
                logger.debug("Failed to update popout thinking label")
        self._thinking_after_id = self.after(400, self._animate_thinking)

    # ------------------------------------------------------------------
    # Visual indicators
    # ------------------------------------------------------------------

    def _show_learned_indicator(self) -> None:
        """Show a brief 'Learned' indicator that fades after 2s."""
        self.output.config(state=tk.NORMAL)
        marker = self.output.index(tk.END)
        self.output.insert(tk.END, "  Learned\n", "learned")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

        def _remove() -> None:
            try:
                self.output.config(state=tk.NORMAL)
                self.output.delete(marker, f"{marker}+1l")
                self.output.config(state=tk.DISABLED)
            except tk.TclError:
                logger.debug(
                    "Failed to remove learned indicator (widget may be destroyed)"
                )

        self.after(2000, _remove)

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

    # ------------------------------------------------------------------
    # Help overlay
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        """Show help overlay with commands and tips."""
        help_win = tk.Toplevel(self)
        help_win.title("Jarvis Help")
        help_win.geometry("420x480")
        help_win.configure(bg="#0a1628")
        help_win.attributes("-topmost", True)
        help_win.resizable(False, False)

        tk.Label(
            help_win,
            text="Jarvis Help",
            bg="#0a1628",
            fg="#e2e8f0",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(16, 8))

        sections = [
            (
                "Talking to Jarvis",
                [
                    "Type any question or command, press Enter or Send",
                    "Voice Dictate or say 'Jarvis' (wake word) for voice input",
                    "Say 'done' or click End Conversation when finished",
                    "Jarvis keeps context across commands in a conversation",
                ],
            ),
            (
                "Teaching Jarvis",
                [
                    '"Remember that [fact]" -- saves to memory',
                    '"What do you know about [topic]?" -- queries memory',
                    '"Forget about [topic]" -- removes from knowledge base',
                    "Jarvis auto-learns from every conversation",
                ],
            ),
            (
                "Quick Commands",
                [
                    '"Knowledge status" -- brain health report',
                    '"System status" -- service health check',
                    '"Mission status" -- active learning missions',
                    '"Search the web for [topic]" -- web-augmented answers',
                ],
            ),
            (
                "Control Buttons",
                [
                    "Pause -- stops daemon (background tasks, alerts, learning)",
                    "Resume -- restarts daemon after pause",
                    "Safe Mode -- forces local Ollama (no cloud) for privacy",
                    "Refresh -- shows intelligence score and memory stats",
                    "Diagnose -- runs self-healing, repairs DB, creates snapshot",
                    "Activity -- shows last 20 events (commands, learning, alerts)",
                    "End Conversation -- clears context and starts fresh",
                ],
            ),
            (
                "Keyboard Shortcuts",
                [
                    "Enter -- Send command",
                    "Ctrl+Enter -- Send command (alternative)",
                    "Shift+Enter -- New line (don't send)",
                    "Escape -- Close this help window",
                ],
            ),
        ]

        text = tk.Text(
            help_win,
            wrap=tk.WORD,
            bg="#0a1628",
            fg="#cbd5e1",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=16,
            pady=8,
            state=tk.DISABLED,
            highlightthickness=0,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        text.tag_configure(
            "heading", foreground="#6ee7b7", font=("Segoe UI", 11, "bold")
        )
        text.tag_configure("item", foreground="#cbd5e1", font=("Segoe UI", 10))
        text.config(state=tk.NORMAL)
        for heading, items in sections:
            text.insert(tk.END, f"\n{heading}\n", "heading")
            for item in items:
                text.insert(tk.END, f"  {item}\n", "item")
        text.config(state=tk.DISABLED)

        help_win.bind("<Escape>", lambda _: help_win.destroy())
        help_win.focus_set()
