from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import tkinter as tk
from tkinter import messagebox

from jarvis_engine._constants import DEFAULT_API_PORT as _DEFAULT_PORT, DEFAULT_CLOUD_MODEL
from jarvis_engine._shared import env_int as _env_int
from jarvis_engine.widget_conversation import ConversationMixin
from jarvis_engine.widget_orb import OrbAnimationMixin
from jarvis_engine.widget_tray import TrayMixin
from jarvis_engine.widget_helpers import (  # noqa: F401 -- re-exported for tests
    WidgetConfig,
    _DPAPI_AVAILABLE,
    _TOAST_COOLDOWN_SECONDS,
    _TOAST_ICON_TYPES,
    _TOAST_MAX_MESSAGE,
    _TOAST_MAX_TITLE,
    _Tooltip,
    _create_tray_icon_image,
    _detect_hotword_once,
    _dpapi_decrypt,
    _dpapi_encrypt,
    _get_ssl_context,
    _http_error_details,
    _http_json,
    _http_json_bootstrap,
    _is_position_on_screen,
    _is_safe_widget_base_url,
    _load_mobile_api_cfg,
    _load_widget_cfg,
    _mobile_api_cfg_path,
    _repo_root,
    _save_widget_cfg,
    _security_dir,
    _show_toast,
    _signed_headers,
    _snap_to_edge,
    _voice_dictate_once,
    _widget_cfg_path,
)

logger = logging.getLogger(__name__)


class JarvisDesktopWidget(OrbAnimationMixin, ConversationMixin, TrayMixin, tk.Tk):
    BG = "#070d1a"
    PANEL = "#0d1628"
    EDGE = "#1e3250"
    TEXT = "#dce8ff"
    MUTED = "#8ea4c5"
    ACCENT = "#12c9b1"
    ACCENT_2 = "#1aa3ff"
    WARN = "#d15a5a"
    LAUNCHER_TRANSPARENT = "#010203"

    # Model rotation: (alias, display_name, best_use_title, accent_color)
    # "auto" lets IntentClassifier decide; others override the model choice.
    # Immutable tuple-of-tuples to prevent accidental mutation of shared state.
    MODEL_ROTATION: tuple[tuple[str, str, str, str], ...] = (
        ("auto", "Auto", "Smart Router", "#12c9b1"),
        # CLI-based models (subscription plans, no API keys needed)
        ("claude-cli", "Claude CLI", "Opus 4.6 · Code & Architecture", "#d946ef"),
        ("codex-cli", "Codex CLI", "GPT-5.3 · Math & Logic", "#10b981"),
        ("gemini-cli", "Gemini CLI", "Creative & Web Research", "#f59e0b"),
        ("kimi-cli", "Kimi CLI", "General Purpose", "#e879f9"),
        # Cloud API models
        (DEFAULT_CLOUD_MODEL, "Kimi K2", "Fast API · Primary Operator", "#f59e0b"),
        ("llama-3.3-70b", "LLaMA 3.3 70B", "Fast Analyst", "#3b82f6"),
        ("devstral-2", "Devstral 2", "Code Specialist", "#8b5cf6"),
        ("glm-4.7-flash", "GLM-4.7 Flash", "Speed Runner", "#ef4444"),
        # Anthropic API models (require ANTHROPIC_API_KEY)
        ("claude-opus", "Claude Opus", "Deep Reasoner (API)", "#d946ef"),
        ("claude-sonnet", "Claude Sonnet", "Balanced Thinker (API)", "#06b6d4"),
        ("claude-haiku", "Claude Haiku", "Rapid Responder (API)", "#22c55e"),
        # Specialty aliases
        ("planner-cli", "Planner", "Opus 4.6 · Strategy & Research", "#c084fc"),
    )

    def __init__(self, root_path: Path) -> None:
        super().__init__()
        self.root_path = root_path
        self.cfg = _load_widget_cfg(root_path)
        self.stop_event = threading.Event()
        self.online = False
        self._anim_t0: float = time.monotonic()
        self._launcher_size = 96
        self.launcher_win: tk.Toplevel | None = None
        self.launcher_canvas: tk.Canvas | None = None
        self._l_arc1: int | None = None
        self._l_arc2: int | None = None
        self._l_arc3: int | None = None
        self._l_arc4: int | None = None
        self._l_core: int | None = None
        self._l_glow: int | None = None
        self._l_particles: list[int] = []
        self._orb_sweep: int | None = None
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._launcher_dragged = False
        self._hotword_active = threading.Event()  # Guards against multiple hotword loops
        self._orb_after_id: str | None = None
        self._launcher_after_id: str | None = None
        self._prev_svc_running: dict[str, bool] = {}  # Track service state for crash detection
        self._widget_state: str = "idle"  # idle | listening | processing | error
        self._thinking_after_id: str | None = None  # after() id for dot animation
        self._thinking_dots: int = 3
        self._thinking_start_time: float = 0.0  # When thinking started (time.time())
        self._cmd_generation: int = 0  # Generation counter for race condition prevention
        self._processing_timeout_id: str | None = None  # Safety timeout for stuck processing
        self._cancel_event = threading.Event()  # Set to cancel current command
        self._welcome_shown: bool = False  # One-time welcome message flag
        self._error_clear_id: str | None = None  # after() id for auto-clearing error state
        self._position_save_id: str | None = None  # debounce timer for position save
        self._SNAP_DISTANCE = 20  # pixels from screen edge to trigger snap
        self._tray_icon: Any = None  # pystray.Icon instance (or None if unavailable)
        self._model_index: int = 0  # Index into MODEL_ROTATION (0 = Auto)
        self._model_label: tk.Label | None = None  # Model indicator label widget
        self._seen_event_ids: dict[str, None] = {}  # Ordered dedup for activity feed events
        self._processing_timeout_ms = _env_int(
            "JARVIS_WIDGET_PROCESSING_TIMEOUT_MS",
            300_000,
            minimum=30_000,
            maximum=1_800_000,
        )

        self.title("Jarvis Unlimited")
        # Restore saved panel position if available and on-screen
        _px, _py = self.cfg.panel_x, self.cfg.panel_y
        if _px is not None and _py is not None:
            self.geometry(f"470x840+{_px}+{_py}")
        else:
            self.geometry("470x840+40+60")
        self.minsize(420, 620)
        self.configure(bg=self.BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._build_launcher()
        self._build_tray_icon()
        self._bind_shortcuts()
        self._start_status_workers()
        self._animate_orb()
        self._animate_launcher()
        self._hide_panel()
        self._log("Jarvis Widget started. Checking connection...", role="system")
        self._log("Enter sends command, Shift+Enter inserts newline.", role="system")
        # Show initial connection status after first health check
        self._thread(self._startup_status_check)

    def _startup_status_check(self) -> None:
        """Quick connection check on startup so user sees status immediately."""
        time.sleep(1.5)  # Give health loop a moment to poll
        if self.stop_event.is_set():
            return
        if self.online:
            self._log_async("Connected to Jarvis services.", role="jarvis")
        else:
            self._log_async("OFFLINE - Cannot reach Jarvis services.", role="error")
            self._log_async("Start services with: jarvis-engine daemon", role="system")
            self._log_async("Then click Connect to authenticate.", role="system")

    def _on_close(self) -> None:
        """Handle window close (X button): minimize to launcher orb."""
        self._hide_panel()

    def _kill_child_services(self) -> None:
        """Kill mobile API and daemon processes spawned alongside the widget."""
        try:
            from jarvis_engine.process_manager import read_pid_file, kill_service
            root = self.root_path if hasattr(self, "root_path") else _repo_root()
            for service in ("mobile_api", "daemon"):
                try:
                    info = read_pid_file(service, root)
                    if info is not None:
                        kill_service(service, root)
                        logger.info("Killed %s (pid=%s) on widget shutdown.", service, info.get("pid"))
                except (OSError, ValueError) as exc:
                    logger.debug("Failed to kill %s on shutdown: %s", service, exc)
        except ImportError:
            # Fallback: kill by command line pattern
            try:
                import subprocess
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process | Where-Object {"
                     "($_.Name -eq 'python.exe') -and "
                     "$_.CommandLine -match 'jarvis_engine.main\\s+(daemon-run|serve-mobile)'"
                     "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                    capture_output=True, timeout=10,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("Fallback process kill failed: %s", exc)

    def _confirm_exit(self) -> None:
        """Show confirmation dialog before shutting down."""
        msg = (
            "Are you sure you want to exit Jarvis?\n\n"
            "This will terminate all Jarvis services:\n"
            "  \u2022 Desktop Widget\n"
            "  \u2022 Mobile API Server\n"
            "  \u2022 Background Daemon\n\n"
            "For best results before exiting:\n"
            "  1. Let any active command finish processing\n"
            "  2. Memory is auto-saved (SQLite WAL mode),\n"
            "     but in-flight learning cycles will be lost\n"
            "  3. Knowledge graph writes are transactional\n"
            "     and safe to interrupt\n\n"
            "Tip: Use 'Minimize' or the X button to keep\n"
            "Jarvis running in the background instead."
        )
        confirmed = messagebox.askyesno(
            "Exit Jarvis",
            msg,
            icon=messagebox.WARNING,
            parent=self,
        )
        if confirmed:
            self._shutdown()

    def _shutdown(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        self.stop_event.set()
        self._stop_tray_icon()
        # Kill child services (mobile API, daemon) before destroying widget
        self._kill_child_services()
        # Cancel pending animation callbacks to prevent post-destroy TclError
        if self._orb_after_id is not None:
            try:
                self.after_cancel(self._orb_after_id)
            except (tk.TclError, RuntimeError) as exc:
                logger.debug("Failed to cancel orb animation callback: %s", exc)
        if self._launcher_after_id is not None:
            try:
                self.after_cancel(self._launcher_after_id)
            except (tk.TclError, RuntimeError) as exc:
                logger.debug("Failed to cancel launcher animation callback: %s", exc)
        # Wait briefly for background threads to finish
        for t in threading.enumerate():
            if t.daemon and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.0)
        if self.launcher_win is not None:
            try:
                self.launcher_win.destroy()
            except Exception as exc:  # boundary: catch-all justified
                logger.debug("Failed to destroy launcher window during shutdown: %s", exc)
        try:
            self.destroy()
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to destroy main widget window during shutdown: %s", exc)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-space>", lambda _e: self._toggle_min())
        self.bind("<Escape>", lambda _e: self._on_escape())
        self.bind("<Control-Return>", lambda _e: self._send_command_async())
        self.bind("<Control-Shift-Q>", lambda _e: self._confirm_exit())
        self.bind("<Configure>", self._on_panel_configure)

    def _toggle_min(self) -> None:
        if self.state() in {"withdrawn", "iconic"}:
            self._show_panel()
        else:
            self._hide_panel()

    def _show_panel(self) -> None:
        # Restore saved position if valid
        px, py = self.cfg.panel_x, self.cfg.panel_y
        if px is not None and py is not None and _is_position_on_screen(px, py, self):
            self.geometry(f"+{px}+{py}")
        self.deiconify()
        self.lift()
        self.focus_force()
        if self.launcher_win is not None:
            self.launcher_win.withdraw()

    def _hide_panel(self) -> None:
        self.withdraw()
        # Always show the launcher orb so the user has a visible click target.
        # The system tray icon (if available) is a supplementary access method,
        # not a replacement — it may be hidden in the Windows 11 overflow area.
        if self.launcher_win is not None:
            self.launcher_win.deiconify()
            self.launcher_win.lift()

    def _on_panel_configure(self, event: tk.Event[Any]) -> None:
        """Handle panel move/resize -- debounce position save and snap to edge."""
        # Only process events from the root window itself, not child widgets
        if event.widget is not self:
            return
        # Debounce: cancel previous timer, schedule a new save in 300ms
        if self._position_save_id is not None:
            try:
                self.after_cancel(self._position_save_id)
            except (tk.TclError, RuntimeError):  # Widget may be destroyed
                logger.debug("Failed to cancel position save timer (widget may be destroyed)")
        self._position_save_id = self.after(300, self._save_panel_position)

    def _save_panel_position(self) -> None:
        """Save the current panel position to config (with edge snap)."""
        try:
            x = self.winfo_x()
            y = self.winfo_y()
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Cannot read panel position (widget may be destroyed)")
            return
        x, y = _snap_to_edge(x, y, self.winfo_width(), self.winfo_height(), self)
        # Apply snapped position
        try:
            self.geometry(f"+{x}+{y}")
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Failed to apply snapped panel geometry (widget may be destroyed)")
        self.cfg.panel_x = x
        self.cfg.panel_y = y
        try:
            _save_widget_cfg(self.root_path, self.cfg)
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to save widget position to config: %s", exc)

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=self.BG, bd=0)
        shell.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._build_toolbar(shell)

        body = tk.Frame(shell, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._build_command_area(body)
        self._build_status_bar(body)
        self._build_chat_area(body)

        # Tooltips on key controls
        _Tooltip(self.command_text, "Type a command or question")
        _Tooltip(self._voice_btn, "Click or say 'Jarvis' to dictate")
        _Tooltip(self._send_btn, "Send command (Enter)")

    def _build_toolbar(self, shell: tk.Frame) -> None:
        """Build the header toolbar with title, buttons, status orb, and intel label."""
        header = tk.Frame(shell, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1)
        header.pack(fill=tk.X)

        top = tk.Frame(header, bg=self.PANEL)
        top.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(top, text="Jarvis Unlimited", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        tk.Button(
            top,
            text="Minimize",
            bg="#10213a",
            fg=self.TEXT,
            activebackground="#173158",
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            command=self._hide_panel,
        ).pack(side=tk.RIGHT)
        tk.Button(
            top,
            text="Exit",
            bg="#2a1111",
            fg="#fecaca",
            activebackground="#4a1b1b",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            command=self._confirm_exit,
        ).pack(side=tk.RIGHT, padx=(0, 6))
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

        status_row = tk.Frame(header, bg=self.PANEL)
        status_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.orb_canvas = tk.Canvas(status_row, width=30, height=30, bg=self.PANEL, highlightthickness=0)
        self.orb_canvas.pack(side=tk.LEFT)
        self._orb_sweep = self.orb_canvas.create_arc(
            2, 2, 28, 28, start=0, extent=90,
            style=tk.ARC, outline=self.ACCENT, width=1,
        )
        self.orb_id = self.orb_canvas.create_oval(9, 9, 21, 21, fill="#6366f1", outline="")
        self.status_var = tk.StringVar(value="CONNECTING...")
        self.status_label = tk.Label(status_row, textvariable=self.status_var, bg=self.PANEL, fg="#a5b4fc", font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=(6, 0))
        self.intel_var = tk.StringVar(value="")
        self.intel_label = tk.Label(status_row, textvariable=self.intel_var, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9, "bold"))
        self.intel_label.pack(side=tk.RIGHT, padx=(0, 8))
        tk.Label(status_row, text="Hotword: say 'Jarvis'", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9)).pack(side=tk.RIGHT)

    def _build_command_area(self, body: tk.Frame) -> None:
        """Build the session config, command input, flags, action buttons, and quick actions."""
        self._build_session_config(body)
        self._build_command_input(body)
        self._build_command_flags(body)
        self._build_action_buttons(body)
        self._build_quick_actions(body)
        self._build_fetch_buttons(body)

    def _build_session_config(self, body: tk.Frame) -> None:
        """Build the Secure Session config section with URL, password, and advanced fields."""
        sec = tk.LabelFrame(body, text="Secure Session", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        sec.pack(fill=tk.X, padx=10, pady=(10, 8))

        self.base_var = tk.StringVar(value=self.cfg.base_url)
        self.token_var = tk.StringVar(value=self.cfg.token)
        self.key_var = tk.StringVar(value=self.cfg.signing_key)
        self.device_var = tk.StringVar(value=self.cfg.device_id)
        self.master_var = tk.StringVar(value=self.cfg.master_password)

        self._entry(sec, "Base URL", self.base_var)
        self._entry(sec, "Master password", self.master_var, show="*")

        # Advanced fields (hidden by default)
        self._adv_visible = tk.BooleanVar(value=False)
        adv_toggle = tk.Frame(sec, bg=self.PANEL)
        adv_toggle.pack(fill=tk.X, padx=6, pady=(2, 0))
        self._adv_toggle_btn = tk.Button(
            adv_toggle,
            text="\u25B6 Advanced",
            bg=self.PANEL,
            fg=self.MUTED,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            cursor="hand2",
            command=self._toggle_advanced,
        )
        self._adv_toggle_btn.pack(side=tk.LEFT)
        self._adv_frame = tk.Frame(sec, bg=self.PANEL)
        self._entry(self._adv_frame, "Bearer token", self.token_var)
        self._entry(self._adv_frame, "Signing key", self.key_var)
        self._entry(self._adv_frame, "Device ID", self.device_var)
        # Advanced frame hidden by default (not packed)

        self._sec_buttons = tk.Frame(sec, bg=self.PANEL)
        self._sec_buttons.pack(fill=tk.X, padx=6, pady=(4, 8))
        tk.Button(
            self._sec_buttons,
            text="Save",
            bg="#133d70",
            fg="#eaf3ff",
            relief=tk.FLAT,
            command=self._save_session,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(
            self._sec_buttons,
            text="Connect",
            bg="#0f766e",
            fg="#ecfeff",
            relief=tk.FLAT,
            command=self._bootstrap_session_async,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_command_input(self, body: tk.Frame) -> None:
        """Build the command text area with model indicator row."""
        cmd_block = tk.Frame(body, bg=self.PANEL)
        cmd_block.pack(fill=tk.X, padx=10)
        tk.Label(cmd_block, text="Command", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.command_text = tk.Text(
            cmd_block,
            height=5,
            wrap=tk.WORD,
            bg="#081127",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
            font=("Consolas", 11),
        )
        self.command_text.pack(fill=tk.X, pady=(4, 4))
        self.command_text.bind("<Return>", self._on_command_enter)
        self.command_text.bind("<Tab>", self._on_tab_cycle_model)

        # Model indicator row
        model_row = tk.Frame(cmd_block, bg=self.PANEL)
        model_row.pack(fill=tk.X, pady=(0, 2))
        _m = self.MODEL_ROTATION[0]
        self._model_label = tk.Label(
            model_row,
            text=f"\u21b9 Tab  {_m[1]} \u00b7 {_m[2]}",
            bg=self.PANEL,
            fg=_m[3],
            font=("Segoe UI", 9),
            anchor="w",
        )
        self._model_label.pack(side=tk.LEFT)
        _Tooltip(self._model_label, "Press Tab to cycle through available LLM models")

    def _build_command_flags(self, body: tk.Frame) -> None:
        """Build the checkbox flags row (Allow PC Actions, Speak, Wake Word, etc.)."""
        flags = tk.Frame(body, bg=self.PANEL)
        flags.pack(fill=tk.X, padx=10, pady=(2, 0))
        self.execute_var = tk.BooleanVar(value=True)
        self.priv_var = tk.BooleanVar(value=False)
        self.speak_var = tk.BooleanVar(value=True)
        self.auto_send_var = tk.BooleanVar(value=True)
        self.hotword_var = tk.BooleanVar(value=False)
        self.notify_var = tk.BooleanVar(value=True)
        self._check(flags, "Allow PC Actions", self.execute_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Auto-Approve", self.priv_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Speak", self.speak_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Auto Send", self.auto_send_var).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Wake Word", self.hotword_var, cmd=self._hotword_changed).pack(side=tk.LEFT, padx=(0, 10))
        self._check(flags, "Notifications", self.notify_var).pack(side=tk.LEFT)

    def _build_action_buttons(self, body: tk.Frame) -> None:
        """Build the Voice Dictate, Send, and Stop action buttons."""
        row = tk.Frame(body, bg=self.PANEL)
        row.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._voice_btn = self._btn(row, "Voice Dictate", self._dictate_async, self.ACCENT_2)
        self._voice_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._send_btn = self._btn(row, "Send", self._send_command_async, self.ACCENT)
        self._send_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._cancel_btn = self._btn(row, "\u25A0 Stop", self._cancel_command, "#c0392b")
        self._cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._cancel_btn.pack_forget()  # Hidden by default, shown during processing

    def _build_quick_actions(self, body: tk.Frame) -> None:
        """Build the Pause, Resume, and Safe Mode quick action buttons."""
        quick = tk.Frame(body, bg=self.PANEL)
        quick.pack(fill=tk.X, padx=10, pady=(8, 0))
        _pause_btn = self._btn(quick, "\u23F8 Pause", lambda: self._quick_phrase("Jarvis, pause daemon"), self.WARN)
        _pause_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_pause_btn, "Pause the Jarvis daemon.\nStops background tasks, proactive alerts, and auto-learning.\nUse Resume to restart.")
        _resume_btn = self._btn(quick, "\u25B6 Resume", lambda: self._quick_phrase("Jarvis, resume daemon"), self.ACCENT)
        _resume_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_resume_btn, "Resume the Jarvis daemon.\nRestarts background tasks, proactive alerts, and auto-learning.")
        _safe_btn = self._btn(quick, "\U0001F6E1 Safe Mode", lambda: self._quick_phrase("Jarvis, enable safe mode"), self.ACCENT_2)
        _safe_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(_safe_btn, "Enable Safe Mode.\nForces all queries through local Ollama (no cloud).\nUse for private/sensitive conversations.")

    def _build_fetch_buttons(self, body: tk.Frame) -> None:
        """Build the Refresh, Diagnose, and Activity fetch buttons."""
        fetch = tk.Frame(body, bg=self.PANEL)
        fetch.pack(fill=tk.X, padx=10, pady=(8, 0))
        _refresh_btn = self._btn(fetch, "\U0001F504 Refresh", self._refresh_dashboard_async, "#35517a")
        _refresh_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_refresh_btn, "Refresh the dashboard.\nShows intelligence score, memory stats, and growth trends.")
        _diag_btn = self._btn(fetch, "\U0001F527 Diagnose", self._diagnose_repair_async, "#1f5f88")
        _diag_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        _Tooltip(_diag_btn, "Run self-healing diagnostics.\nChecks DB integrity, repairs broken indexes,\nand creates a recovery snapshot.")
        _activity_btn = self._btn_lg(fetch, "\U0001F4CA Activity", self._view_activity_async, "#4a3570")
        _activity_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(_activity_btn, "View recent activity log.\nShows last 20 events: commands, learning,\nalerts, and system actions.")

    def _build_status_bar(self, body: tk.Frame) -> None:
        """Build the Running Services and Brain Growth status sections."""
        # Running Services section
        svc_frame = tk.LabelFrame(body, text="Running Services", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        svc_frame.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._svc_labels: dict[str, tuple[tk.Label, tk.Label]] = {}
        for svc_name, display_name in [("daemon", "Assistant"), ("mobile_api", "Mobile API"), ("widget", "Widget")]:
            row_f = tk.Frame(svc_frame, bg=self.PANEL)
            row_f.pack(fill=tk.X, padx=6, pady=2)
            dot = tk.Label(row_f, text="\u25CB", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10))
            dot.pack(side=tk.LEFT)
            tk.Label(row_f, text=display_name, bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 0))
            uptime_lbl = tk.Label(row_f, text="--", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9))
            uptime_lbl.pack(side=tk.RIGHT)
            self._svc_labels[svc_name] = (dot, uptime_lbl)
        self._refresh_services()

        # Brain Growth section
        growth_frame = tk.LabelFrame(body, text="Brain Growth", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        growth_frame.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._growth_labels: dict[str, tk.Label] = {}
        for key, display in [
            ("facts", "Facts"),
            ("kg", "KG Size"),
            ("memory", "Memory"),
            ("missions", "Missions"),
            ("score", "Self-Test"),
            ("trend", "Trend"),
        ]:
            row_f = tk.Frame(growth_frame, bg=self.PANEL)
            row_f.pack(fill=tk.X, padx=6, pady=1)
            tk.Label(row_f, text=display, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9), width=10, anchor="w").pack(side=tk.LEFT)
            val = tk.Label(row_f, text="--", bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 9, "bold"), anchor="w")
            val.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
            self._growth_labels[key] = val

    def _toggle_advanced(self) -> None:
        """Show/hide advanced session fields (token, signing key, device ID)."""
        if self._adv_visible.get():
            self._adv_frame.pack_forget()
            self._adv_visible.set(False)
            self._adv_toggle_btn.config(text="\u25B6 Advanced")
        else:
            self._adv_frame.pack(fill=tk.X, padx=0, pady=(0, 0), before=self._sec_buttons)
            self._adv_visible.set(True)
            self._adv_toggle_btn.config(text="\u25BC Advanced")

    def _entry(self, parent: tk.Widget, label: str, var: tk.StringVar, show: str | None = None) -> None:
        tk.Label(parent, text=label, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=6, pady=(4, 0))
        tk.Entry(
            parent,
            textvariable=var,
            show=show or "",
            bg="#081127",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief=tk.FLAT,
            highlightbackground="#2a4368",
            highlightthickness=1,
        ).pack(fill=tk.X, padx=6, pady=(2, 0))

    def _check(self, parent: tk.Widget, text: str, variable: tk.BooleanVar, cmd: Callable[[], None] | None = None) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=cmd,
            bg=self.PANEL,
            fg=self.MUTED,
            selectcolor="#1a2742",
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
        )

    def _btn(self, parent: tk.Widget, text: str, command: Callable[[], None], color: str) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="#eef8ff",
            activebackground=color,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=8,
            pady=6,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )

    def _btn_lg(self, parent: tk.Widget, text: str, command: Callable[[], None], color: str) -> tk.Button:
        """Larger variant of _btn for primary action buttons."""
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="#eef8ff",
            activebackground=color,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            font=("Segoe UI", 12, "bold"),
            cursor="hand2",
        )

    def _on_command_enter(self, event: tk.Event[Any]) -> str | None:
        if event.state & 0x0001:  # Shift key pressed
            return None
        self._send_command_async()
        return "break"

    def _notify_toast(self, title: str, message: str, icon: str = "Info") -> None:
        """Send a toast notification if the Notifications toggle is enabled."""
        try:
            if self.notify_var.get():
                _show_toast(title, message, icon)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed; toast is best-effort
            logger.debug("Toast notification failed (widget may be destroyed)")

    def _set_command_text(self, value: str) -> None:
        self.command_text.delete("1.0", tk.END)
        self.command_text.insert("1.0", value)

    def _set_command_text_async(self, value: str) -> None:
        self.after(0, self._set_command_text, value)

    def _current_cfg(self) -> WidgetConfig:
        _fallback = f"{'https' if (_security_dir(self.root_path) / 'tls_cert.pem').exists() else 'http'}://127.0.0.1:{_DEFAULT_PORT}"
        return WidgetConfig(
            base_url=self.base_var.get().strip() or _fallback,
            token=self.token_var.get().strip(),
            signing_key=self.key_var.get().strip(),
            device_id=self.device_var.get().strip(),
            master_password=self.master_var.get(),
        )

    def _save_session(self) -> None:
        cfg = self._current_cfg()
        _save_widget_cfg(self.root_path, cfg)
        self._log("Session saved locally.")

    def _apply_session_update(self, session: dict[str, Any]) -> None:
        base_url = str(session.get("base_url", "")).strip()
        token = str(session.get("token", "")).strip()
        signing_key = str(session.get("signing_key", "")).strip()
        device_id = str(session.get("device_id", "")).strip()
        if base_url:
            self.base_var.set(base_url)
        if token:
            self.token_var.set(token)
        if signing_key:
            self.key_var.set(signing_key)
        if device_id:
            self.device_var.set(device_id)
        self._save_session()

    def _bootstrap_session_async(self) -> None:
        cfg = self._current_cfg()
        if not cfg.base_url.strip():
            self._log("Bootstrap failed: missing Base URL.", role="error")
            return
        if not cfg.master_password.strip():
            self._log("Bootstrap failed: enter Master password first.", role="error")
            return

        def worker() -> None:
            try:
                data = _http_json_bootstrap(cfg.base_url, cfg.master_password, cfg.device_id)
                ok = bool(data.get("ok", False))
                session = data.get("session", {})
                if (not ok) or (not isinstance(session, dict)):
                    raise RuntimeError(str(data.get("error", "Bootstrap returned no session data.")))
                self.after(0, self._apply_session_update, session)
                trusted = bool(session.get("trusted_device", False))
                self._log_async(f"Bootstrap complete. trusted_device={trusted}", role="jarvis")
                self.after(0, self._show_welcome)
            except HTTPError as exc:
                self._log_async(f"Connect failed: {_http_error_details(exc)}", role="error")
            except URLError:
                self._log_async("Cannot reach Jarvis server.", role="error")
                self._log_async("Make sure the Mobile API is running and the Base URL is correct.", role="error")
            except (RuntimeError, TimeoutError) as exc:
                self._log_async(f"Connect failed: {exc}", role="error")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Widget connect failed: %s", exc)
                self._log_async(f"Connect failed: {exc}", role="error")

        self._thread(worker)

    def _diag_check_connection(self, cfg: WidgetConfig) -> bool:
        """Diagnostic step 1: check API connection. Returns False if offline."""
        self._log_async("[1/4] Checking API connection...", role="system")
        try:
            health_data = _http_json(cfg, "/health", method="GET")
            health_ok = bool(health_data.get("ok", False))
            self._log_async(f"  API: {'ONLINE' if health_ok else 'DEGRADED'}", role="jarvis")
            return True
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Diagnostics health check failed: %s", exc)
            self._log_async("  API: OFFLINE - cannot reach Jarvis services", role="error")
            self._log_async("  Make sure Mobile API is running (jarvis-engine serve-mobile)", role="system")
            return False

    def _diag_check_sync(self, cfg: WidgetConfig) -> None:
        """Diagnostic step 2: check sync status."""
        self._log_async("[2/4] Checking sync status...", role="system")
        try:
            sync_data = _http_json(cfg, "/sync/status", method="GET")
            sync_ok = bool(sync_data.get("ok", False))
            last_sync = sync_data.get("last_sync_utc", "unknown")
            self._log_async(f"  Sync: {'OK' if sync_ok else 'Issues detected'}", role="jarvis")
            self._log_async(f"  Last sync: {last_sync}", role="jarvis")
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Diagnostics sync check failed: %s", exc)
            self._log_async(f"  Sync: unavailable ({exc})", role="error")

    def _diag_check_intelligence(self, cfg: WidgetConfig) -> None:
        """Diagnostic step 3: test intelligence pipeline."""
        self._log_async("[3/4] Testing intelligence pipeline...", role="system")
        try:
            dash_data = _http_json(cfg, "/dashboard", method="GET")
            score = dash_data.get("intelligence_score", "?")
            mem_count = dash_data.get("memory_count", "?")
            fact_count = dash_data.get("fact_count", "?")
            self._log_async(f"  Intelligence score: {score}", role="jarvis")
            self._log_async(f"  Memories: {mem_count}, Facts: {fact_count}", role="jarvis")
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Diagnostics intelligence check failed: %s", exc)
            self._log_async(f"  Intelligence: unavailable ({exc})", role="error")

    def _diag_run_self_heal(self, cfg: WidgetConfig) -> None:
        """Diagnostic step 4: run self-heal maintenance and display results."""
        self._log_async("[4/4] Running self-heal maintenance...", role="system")
        heal_data = _http_json(
            cfg,
            "/self-heal",
            method="POST",
            payload={
                "keep_recent": 1800,
                "force_maintenance": True,
                "snapshot_note": "widget-diagnose",
            },
        )
        heal_ok = bool(heal_data.get("ok", False))
        heal_exit = int(heal_data.get("command_exit_code", -1))
        heal_lines = heal_data.get("stdout_tail", [])
        if heal_ok:
            self._log_async("  Self-heal: completed successfully", role="jarvis")
        else:
            self._log_async(f"  Self-heal: finished with issues (exit={heal_exit})", role="error")
        if isinstance(heal_lines, list) and heal_lines:
            for line in heal_lines[-5:]:
                s = str(line).strip()
                if s:
                    self._log_async(f"  {s}", role="jarvis")

        self._log_async("\u2500" * 40, role="system")
        if heal_ok:
            self._log_async("\u2705 All systems healthy.", role="jarvis")
        else:
            self._log_async("\u26A0 Some issues detected. Check details above.", role="error")
            self._notify_toast("Jarvis Self-Heal", f"Self-heal finished with issues (exit={heal_exit})", "Warning")

    def _diagnose_repair_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        def worker() -> None:
            try:
                self._log_async("\u2500" * 40, role="system")
                self._log_async("\U0001F527 JARVIS DIAGNOSTICS", role="system")
                self._log_async("\u2500" * 40, role="system")

                if not self._diag_check_connection(cfg):
                    return
                self._diag_check_sync(cfg)
                self._diag_check_intelligence(cfg)
                self._diag_run_self_heal(cfg)
            except HTTPError as exc:
                self._log_async(f"Diagnose failed: {_http_error_details(exc)}", role="error")
                self._notify_toast("Jarvis", "Diagnose & Repair failed", "Error")
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
                self._log_async("Start with: jarvis-engine daemon", role="system")
            except (RuntimeError, TimeoutError) as exc:
                self._log_async(f"Diagnose failed: {exc}", role="error")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Diagnose and repair failed: %s", exc)
                self._log_async(f"Diagnose failed: {exc}", role="error")

        self._thread(worker)

    def _thread(self, fn: Callable[..., Any]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _on_tab_cycle_model(self, event: tk.Event[Any] | None = None) -> str:
        """Tab key in command text: cycle through model rotation."""
        self._model_index = (self._model_index + 1) % len(self.MODEL_ROTATION)
        self._update_model_label()
        return "break"  # Prevent Tab from inserting a tab character

    def _update_model_label(self) -> None:
        """Update the model indicator label to reflect current selection."""
        if self._model_label is None:
            return
        _m = self.MODEL_ROTATION[self._model_index]
        self._model_label.config(
            text=f"\u21b9 Tab  {_m[1]} \u00b7 {_m[2]}",
            fg=_m[3],
        )

    def _selected_model_override(self) -> str:
        """Return the model alias to override, or empty string for Auto routing."""
        alias = self.MODEL_ROTATION[self._model_index][0]
        return "" if alias == "auto" else alias

    def _on_escape(self) -> None:
        """ESC key handler: cancel command if processing, otherwise minimize."""
        if self._widget_state == "processing":
            self._cancel_command()
        else:
            self._toggle_min()

    def _cancel_command(self) -> None:
        """Cancel the current in-progress command, stop TTS, and reset state."""
        self._cancel_event.set()
        self._cancel_processing_timeout()
        self._hide_thinking()
        # Kill any running TTS processes
        try:
            import subprocess
            # Kill edge-tts and PowerShell speech processes
            for proc_name in ["edge-tts", "edge-playback"]:
                subprocess.run(
                    ["taskkill", "/F", "/IM", f"{proc_name}.exe"],
                    capture_output=True, timeout=5,
                )
            # Kill PowerShell speech synthesis if running
            subprocess.run(
                ["powershell", "-Command",
                 "Get-Process | Where-Object {$_.MainWindowTitle -eq '' -and $_.ProcessName -eq 'powershell'} | Stop-Process -Force -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=5,
            )
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to kill TTS/speech processes during cancel: %s", exc)
        self.command_text.config(state=tk.NORMAL)
        self._log("Command cancelled.", role="system")
        self._set_state("idle")

    def _cancel_processing_timeout(self) -> None:
        """Cancel the safety timeout for stuck processing state."""
        if self._processing_timeout_id is not None:
            try:
                self.after_cancel(self._processing_timeout_id)
            except (tk.TclError, RuntimeError):  # Widget may be destroyed
                logger.debug("Failed to cancel processing timeout timer")
            self._processing_timeout_id = None

    def _processing_timed_out(self) -> None:
        """Safety net: force-reset stuck processing state after configured timeout."""
        self._processing_timeout_id = None
        if self._widget_state == "processing":
            self._hide_thinking()
            timeout_s = max(1, self._processing_timeout_ms // 1000)
            self._log(f"Command timed out ({timeout_s}s). Ready for new commands.", role="error")
            self.command_text.config(state=tk.NORMAL)
            self._set_state("idle")

    def _handle_command_error(self, message: str) -> None:
        """Log a command error with a ready prompt and briefly flash the error state."""
        self._log_async(message, role="error")
        self._log_async("Ready for next command.", role="system")
        self._set_error_briefly_async()

    def _validate_and_prepare_command(self) -> str | None:
        """Validate command text and handle conversation-ending phrases.

        Returns the command text if valid, or None if the command was handled
        (empty, already processing, or conversation-ending phrase).
        """
        if getattr(self, "_widget_state", "idle") == "processing":
            self._log("Still processing previous command. Please wait...", role="system")
            return None
        self._cancel_event.clear()
        text = self.command_text.get("1.0", tk.END).strip()
        if not text:
            self._log("No command text.")
            return None
        lower = text.lower().strip()
        if lower in ("done", "end conversation", "bye", "goodbye", "that's all", "thats all", "end"):
            self.command_text.delete("1.0", tk.END)
            self._end_conversation()
            return None
        return text

    def _make_worker_cleanup(self, gen: int) -> Callable[[], None]:
        """Create a cleanup callback for a command worker tied to a generation counter."""
        def _cleanup() -> None:
            if self._cmd_generation != gen:
                return  # Stale worker -- a newer command owns the state
            self._cancel_processing_timeout()
            self._hide_thinking()
            try:
                self.command_text.config(state=tk.NORMAL)
            except tk.TclError:
                logger.debug("Failed to re-enable command input after processing")
            if self._widget_state == "processing":
                self._set_state("idle")
        return _cleanup

    def _send_command_async(self) -> None:
        text = self._validate_and_prepare_command()
        if text is None:
            return
        # Clear command text immediately after reading
        self.command_text.delete("1.0", tk.END)
        self._log(text, role="user")
        self._set_state("processing")
        self._show_thinking()
        self.command_text.config(state=tk.DISABLED)
        self._cmd_generation += 1
        gen = self._cmd_generation
        self._cancel_processing_timeout()
        self._processing_timeout_id = self.after(self._processing_timeout_ms, self._processing_timed_out)
        cfg = self._current_cfg()
        execute = bool(self.execute_var.get())
        approve_privileged = bool(self.priv_var.get())
        speak = bool(self.speak_var.get())
        model_override = self._selected_model_override()
        cleanup = self._make_worker_cleanup(gen)

        def worker() -> None:
            try:
                payload: dict[str, Any] = {
                    "text": text,
                    "execute": execute,
                    "approve_privileged": approve_privileged,
                    "speak": speak,
                    "master_password": cfg.master_password,
                }
                if model_override:
                    payload["model_override"] = model_override
                data = _http_json(cfg, "/command", method="POST", payload=payload)
                if self._cancel_event.is_set():
                    return
                self.after(0, self._hide_thinking)
                self.after(0, self._cancel_processing_timeout)
                self._process_worker_response(data, cfg)
            except HTTPError as exc:
                self._handle_command_error(f"Command failed: {_http_error_details(exc)}")
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
                self._set_error_briefly_async()
            except (RuntimeError, TimeoutError) as exc:
                self._handle_command_error(f"Command failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Command execution failed: %s", exc)
                self._handle_command_error(f"Command failed: {exc}")
            finally:
                if not self._cancel_event.is_set():
                    try:
                        self.after(0, cleanup)
                    except (tk.TclError, RuntimeError):  # Widget may be destroyed
                        logger.debug("Failed to schedule post-command cleanup (widget may be destroyed)")

        self._thread(worker)

    @staticmethod
    def _parse_command_output(data: dict[str, Any]) -> dict[str, Any]:
        """Parse structured fields from /command API stdout_tail lines.

        Returns a dict with keys: response_text, reason_text, error_text,
        source_urls, thinking_steps, web_search_used, model_name, provider_name,
        intent, ok, lines.
        """
        lines = data.get("stdout_tail", [])
        response_text = ""
        reason_text = ""
        error_text = str(data.get("error", ""))
        source_urls: list[str] = []
        thinking_steps: list[str] = []
        web_search_used = False
        model_name = ""
        provider_name = ""
        _in_response = False
        if isinstance(lines, list):
            for line in lines:
                s = str(line)
                if s.startswith("response="):
                    response_text = s[len("response="):]
                    _in_response = True
                elif s.startswith("reason=") and not reason_text:
                    reason_text = s[len("reason="):]
                    _in_response = False
                elif s.startswith("error=") and not error_text:
                    error_text = s[len("error="):]
                    _in_response = False
                elif s.startswith("source_"):
                    parts = s.split("=", 1)
                    if len(parts) == 2:
                        source_urls.append(parts[1].strip())
                    _in_response = False
                elif s.startswith("model="):
                    model_name = s[len("model="):]
                    _in_response = False
                elif s.startswith("provider="):
                    provider_name = s[len("provider="):]
                    _in_response = False
                elif s == "web_search_used=true":
                    web_search_used = True
                    _in_response = False
                elif s.startswith("intent="):
                    thinking_steps.append(f"Intent: {s[len('intent='):]}")
                    _in_response = False
                elif s.startswith("finding_"):
                    _in_response = False
                elif _in_response and not any(
                    s.startswith(p) for p in [
                        "status_code=", "voice=", "wav=",
                        "auto_ingest_record_id=", "web_search",
                        "query=", "scanned_url_count=",
                    ]
                ):
                    response_text += "\n" + s
                else:
                    _in_response = False
        return {
            "response_text": response_text,
            "reason_text": reason_text,
            "error_text": error_text,
            "source_urls": source_urls,
            "thinking_steps": thinking_steps,
            "web_search_used": web_search_used,
            "model_name": model_name,
            "provider_name": provider_name,
            "intent": str(data.get("intent", "unknown")),
            "ok": bool(data.get("ok", False)),
            "lines": lines,
        }

    def _display_response(self, parsed: dict[str, Any]) -> None:
        """Display the parsed response text, trace info, and source URLs."""
        response_text = parsed["response_text"]
        reason_text = parsed["reason_text"]
        error_text = parsed["error_text"]
        model_name = parsed["model_name"]
        provider_name = parsed["provider_name"]
        web_search_used = parsed["web_search_used"]
        thinking_steps = parsed["thinking_steps"]
        source_urls = parsed["source_urls"]
        intent = parsed["intent"]
        ok = parsed["ok"]
        lines = parsed["lines"]

        # Show processing trace (thinking steps)
        if model_name or web_search_used or thinking_steps:
            trace_parts = []
            if thinking_steps:
                trace_parts.extend(thinking_steps)
            if web_search_used:
                trace_parts.append("Web search: performed")
            if model_name:
                via = f"{model_name}"
                if provider_name:
                    via += f" ({provider_name})"
                trace_parts.append(f"Model: {via}")
            self._log_async("\u2699 " + " \u2022 ".join(trace_parts), role="system")

        if response_text:
            self._log_async(response_text, role="jarvis")
        elif not ok and (reason_text or error_text):
            msg = reason_text or error_text
            self._log_async(f"Error: {msg}", role="error")
        else:
            self._log_async(f"[{intent}] ok={ok}", role="jarvis")
            if isinstance(lines, list) and lines:
                self._log_async(" | ".join(str(x) for x in lines[-6:]), role="jarvis")

        if source_urls:
            self._log_async("\U0001f310 Sources: " + " | ".join(source_urls[:4]), role="system")

    def _handle_post_response_actions(self, parsed: dict[str, Any], cfg: WidgetConfig) -> None:
        """Handle learned indicators, dashboard refresh, and state updates after a response."""
        intent = parsed["intent"]
        ok = parsed["ok"]

        _LEARNED_INTENTS = (
            "memory_ingest", "memory_forget", "llm_conversation",
            "mission_create", "mission_run",
            "harvest", "fact_extracted",
        )
        if ok and intent in _LEARNED_INTENTS:
            self.after(0, self._show_learned_indicator)

        _REFRESH_INTENTS = (
            "mission_cancel", "mission_create", "mission_run",
            "brain_status", "memory_ingest", "memory_forget",
            "harvest",
        )
        if ok and intent in _REFRESH_INTENTS:
            try:
                ws = _http_json(cfg, "/widget-status", method="GET")
                growth_data = ws.get("growth") if isinstance(ws, dict) else None
                recent_evts = ws.get("recent_events", []) if isinstance(ws, dict) else []
                self.after(0, self._update_growth_labels, growth_data)
                if recent_evts:
                    self.after(0, self._update_activity_events, recent_evts)
            except Exception as exc:  # boundary: catch-all justified
                logger.debug("Best-effort dashboard refresh after command failed: %s", exc)

        if not ok:
            self._set_error_briefly_async()
        else:
            self._set_state_async("idle")
        self._log_async("Ready for next command. Say 'done' or click End Conversation when finished.", role="system")

    def _process_worker_response(self, data: dict[str, Any], cfg: WidgetConfig) -> None:
        """Parse and display the response from a /command API call.

        Called from the background worker thread.  Uses ``_log_async`` and
        ``self.after()`` for thread-safe UI updates.
        """
        parsed = self._parse_command_output(data)
        self._display_response(parsed)
        self._handle_post_response_actions(parsed, cfg)

    def _send_text(self, text: str) -> None:
        """Set the command text and send it -- convenience for programmatic use."""
        self._set_command_text(text)
        self._send_command_async()

    def _voice_dictate(self) -> None:
        """Convenience alias for tray menu / external callers."""
        self._dictate_async()

    def _quick_phrase(self, text: str) -> None:
        self._set_command_text(text)
        self._send_command_async()

    def _refresh_services(self) -> None:
        """Update service status dots directly from process_manager (no HTTP)."""
        # Skip expensive refresh when the widget window is not visible
        try:
            if not self.winfo_viewable():
                self.after(10000, self._refresh_services)
                return
        except tk.TclError:
            # Window may be destroyed; bail out without rescheduling
            return
        try:
            from jarvis_engine.process_manager import list_services
            root = _repo_root()
            services = list_services(root)
            for svc in services:
                name = svc["service"]
                if name not in self._svc_labels:
                    continue
                dot, uptime_lbl = self._svc_labels[name]
                if svc["running"]:
                    dot.config(text="\u2022", fg="#22c55e")
                    s = svc.get("uptime_seconds", 0)
                    if s < 60:
                        uptime_lbl.config(text=f"{s}s")
                    elif s < 3600:
                        uptime_lbl.config(text=f"{s // 60}m")
                    else:
                        uptime_lbl.config(text=f"{s // 3600}h {(s % 3600) // 60}m")
                else:
                    dot.config(text="\u25CB", fg=self.MUTED)
                    uptime_lbl.config(text="stopped")
                    # Notify if service was previously running (crash detected)
                    if self._prev_svc_running.get(name, False):
                        self._notify_toast("Jarvis Service Down", f"{name} has stopped", "Warning")
                self._prev_svc_running[name] = svc["running"]
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to refresh service status: %s", exc)
        # Re-schedule every 10 seconds
        self.after(10000, self._refresh_services)

    def _refresh_dashboard_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        def worker() -> None:
            try:
                data = _http_json(cfg, "/dashboard", method="GET")
                dash = data.get("dashboard", {})
                jar = dash.get("jarvis", {}) if isinstance(dash, dict) else {}
                mem = dash.get("memory_regression", {}) if isinstance(dash, dict) else {}
                self._log_async(
                    f"score={jar.get('score_pct', 0.0)} delta={jar.get('delta_vs_prev_pct', 0.0)} "
                    f"memory={mem.get('status', 'unknown')}",
                    role="jarvis",
                )
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Dashboard refresh failed: %s", exc)
                self._log_async(f"Dashboard failed: {exc}", role="error")

        self._thread(worker)

    def _view_activity_async(self) -> None:
        cfg = self._current_cfg()  # Read tkinter vars on main thread

        # Category -> color map for output log
        _CAT_COLORS = {
            "error": "#ef4444",
            "security": "#ef4444",
            "llm_routing": "#3b82f6",
            "fact_extracted": "#22c55e",
            "proactive_trigger": "#eab308",
            "daemon_cycle": "#8ea4c5",
            "harvest": "#14b8a6",
            "voice": "#a78bfa",
        }

        def worker() -> None:
            try:
                data = _http_json(cfg, "/activity?limit=20", method="GET")
                events = data.get("events", [])
                stats = data.get("stats", {})
                if stats:
                    parts = [f"{k}:{v}" for k, v in stats.items()]
                    self._log_async(f"Activity (24h): {', '.join(parts)}", role="jarvis")
                if not events:
                    self._log_async("No recent activity events.", role="system")
                    return
                for evt in reversed(events):
                    ts_raw = str(evt.get("timestamp", ""))
                    ts_short = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
                    cat = str(evt.get("category", ""))
                    summary = str(evt.get("summary", ""))
                    role = "error" if cat == "error" else "jarvis"
                    self._log_async(f"[{ts_short}] [{cat.upper()}] {summary}", role=role)
            except URLError:
                self._log_async("Cannot connect to Jarvis services.", role="error")
                self._log_async("Make sure the Assistant and Mobile API are running.", role="error")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Activity log load failed: %s", exc)
                self._log_async(f"Could not load activity: {exc}", role="error")

        self._thread(worker)

    def _dictate_async(self) -> None:
        # Guard: skip if already listening or processing to prevent overlapping voice
        if getattr(self, "_widget_state", "idle") in ("listening", "processing"):
            return
        auto_send = bool(self.auto_send_var.get())
        self._set_state("listening")

        def worker() -> None:
            try:
                text = _voice_dictate_once(timeout_s=8)
                if not text:
                    self._log_async("No speech recognized.", role="system")
                    self._set_state_async("idle")
                    return
                self._set_command_text_async(text)
                self._log_async(f"dictated: {text}", role="system")
                if auto_send:
                    # Reset to idle first so _send_command_async doesn't
                    # reject the command with "Still processing"
                    self._set_state_async("idle")
                    self.after(0, self._send_command_async)
                else:
                    self._set_state_async("idle")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Voice dictation failed: %s", exc)
                self._log_async(f"dictation failed: {exc}", role="error")
                self._set_error_briefly_async()

        self._thread(worker)

    def _hotword_changed(self) -> None:
        if self.hotword_var.get():
            if self._hotword_active.is_set():
                self._log("Wake Word loop already running.")
                return
            self._log("Wake Word enabled. Say 'Jarvis' to trigger dictation.")
            self._thread(self._hotword_loop)
        else:
            self._log("Wake Word disabled.")

    def _hotword_loop(self) -> None:
        if self._hotword_active.is_set():
            return  # Another loop is already running
        self._hotword_active.set()
        try:
            self._hotword_loop_inner()
        finally:
            self._hotword_active.clear()

    def _hotword_loop_inner(self) -> None:
        def _read_hotword_var() -> bool:
            """Read hotword BooleanVar on main thread."""
            result: list[bool] = [False]
            ready = threading.Event()

            def _read() -> None:
                try:
                    result[0] = bool(self.hotword_var.get())
                except (tk.TclError, RuntimeError):  # Widget may be destroyed
                    result[0] = False
                ready.set()

            try:
                self.after(0, _read)
            except (tk.TclError, RuntimeError):  # Widget may be destroyed
                logger.debug("Cannot schedule hotword state read (widget may be destroyed)")
                return False
            ready.wait(timeout=2.0)
            return result[0]

        while _read_hotword_var() and (not self.stop_event.is_set()):
            try:
                heard = _detect_hotword_once(keyword="jarvis", timeout_s=2)
                if heard and not self.stop_event.is_set():
                    try:
                        self.after(0, self._show_panel)
                        self._log_async("Wake word detected.")
                        self.after(0, self._dictate_async)
                    except (tk.TclError, RuntimeError):  # Widget may be destroyed
                        logger.debug("Cannot schedule wake word actions (widget may be destroyed)")
                        return
            except Exception as exc:  # boundary: catch-all justified
                logger.warning("Hotword detection error: %s", exc)
            # Cooldown: 10s after wake word to avoid re-triggering during processing
            for _ in range(20):
                if self.stop_event.is_set() or (not _read_hotword_var()):
                    return
                time.sleep(0.5)

    def _start_status_workers(self) -> None:
        self._thread(self._health_loop)

    def _probe_health_endpoints(self, cfg: WidgetConfig) -> tuple[bool, dict[str, Any] | None]:
        """Probe health endpoints with localhost fallback. Returns (ok, intel_data)."""
        from urllib.parse import urlparse as _urlparse
        _pu = _urlparse(cfg.base_url)
        _health_urls = [f"{cfg.base_url.rstrip('/')}/health"]
        if _pu.hostname not in ("127.0.0.1", "localhost", "::1"):
            _health_urls.append(f"{_pu.scheme}://127.0.0.1:{_pu.port or _DEFAULT_PORT}/health")

        resp = None
        ok = False
        intel_data: dict[str, Any] | None = None
        for url in _health_urls:
            ssl_ctx = _get_ssl_context(url)
            for _attempt in range(2):
                try:
                    req = Request(url=url, method="GET")
                    resp = urlopen(req, timeout=5, context=ssl_ctx)
                    ok = resp.status == 200
                    if ok:
                        try:
                            body = resp.read().decode("utf-8")
                            health_payload = json.loads(body)
                            if isinstance(health_payload, dict) and "intelligence" in health_payload:
                                intel_data = health_payload["intelligence"]
                        except Exception as exc:  # boundary: catch-all justified
                            logger.debug("Failed to parse intelligence from health response: %s", exc)
                    resp.close()
                    resp = None
                    if ok:
                        break
                except Exception as exc:  # boundary: catch-all justified
                    logger.debug("Health poll request failed: %s", exc)
                    ok = False
                finally:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception as exc:  # boundary: catch-all justified
                            logger.debug("Failed to close health poll HTTP response: %s", exc)
                        resp = None
                if self.stop_event.is_set():
                    break
                time.sleep(0.2)
            if ok or self.stop_event.is_set():
                break
        return ok, intel_data

    def _fetch_widget_status(self, cfg: WidgetConfig) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Fetch growth, alerts, and recent events via /widget-status. Returns (growth_data, recent_events)."""
        growth_data: dict[str, Any] | None = None
        recent_events: list[dict[str, Any]] = []
        try:
            ws = _http_json(cfg, "/widget-status", method="GET")
            growth_data = ws.get("growth") if isinstance(ws, dict) else None
            alerts = ws.get("alerts", []) if isinstance(ws, dict) else []
            recent_events = ws.get("recent_events", []) if isinstance(ws, dict) else []
            if isinstance(alerts, list):
                for alert in alerts:
                    msg = str(alert.get("message", "")) if isinstance(alert, dict) else str(alert)
                    if msg:
                        self._notify_toast("Jarvis Alert", msg, "Warning")
                        break  # One toast per poll cycle
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to fetch widget-status: %s", exc)
        return growth_data, recent_events

    def _health_sleep(self) -> bool:
        """Sleep for 8 seconds in small increments. Returns True if stop_event was set."""
        for _ in range(16):
            if self.stop_event.is_set():
                return True
            time.sleep(0.5)
        return False

    def _health_loop(self) -> None:
        while not self.stop_event.is_set():
            # Schedule tkinter var read on main thread and wait for result
            cfg_holder: list[WidgetConfig | None] = [None]
            ready = threading.Event()

            def _read_cfg(_h=cfg_holder, _r=ready) -> None:
                _h[0] = self._current_cfg()
                _r.set()

            try:
                self.after(0, _read_cfg)
            except (tk.TclError, RuntimeError):  # Widget may be destroyed
                logger.debug("Cannot schedule config read for health loop (widget may be destroyed)")
                return
            ready.wait(timeout=5.0)
            cfg = cfg_holder[0]
            if cfg is None:
                if self._health_sleep():
                    return
                continue
            if not _is_safe_widget_base_url(cfg.base_url):
                try:
                    self.after(0, self._set_online, False)
                except (tk.TclError, RuntimeError):  # Widget may be destroyed
                    logger.debug("Cannot schedule offline state (widget may be destroyed)")
                    return
                if self._health_sleep():
                    return
                continue

            ok, intel_data = self._probe_health_endpoints(cfg)

            growth_data: dict[str, Any] | None = None
            recent_events: list[dict[str, Any]] = []
            if ok and cfg.token and cfg.signing_key:
                growth_data, recent_events = self._fetch_widget_status(cfg)

            if not self.stop_event.is_set():
                try:
                    self.after(0, self._set_online, ok, intel_data, growth_data, recent_events)
                except (tk.TclError, RuntimeError):  # Widget may be destroyed
                    logger.debug("Cannot schedule online state update (widget may be destroyed)")
                    return
            self._health_sleep()

    def _set_online(self, value: bool, intel_data: dict[str, Any] | None = None,
                    growth_data: dict[str, Any] | None = None,
                    recent_events: list[dict[str, Any]] | None = None) -> None:
        """Update online state and refresh status — always call on main thread."""
        self.online = value
        self._update_intelligence_label(intel_data)
        self._update_growth_labels(growth_data)
        if recent_events:
            self._update_activity_events(recent_events)
        self._refresh_status_view()

    def _update_growth_labels(self, growth_data: dict[str, Any] | None) -> None:
        """Update Brain Growth labels from /intelligence/growth metrics."""
        if growth_data is None or not isinstance(growth_data, dict):
            for lbl in self._growth_labels.values():
                lbl.config(text="--", fg=self.MUTED)
            return
        try:
            m = growth_data.get("metrics", growth_data)
            facts_total = int(m.get("facts_total", 0))
            facts_7d = int(m.get("facts_last_7d", 0))
            kg_nodes = int(m.get("kg_nodes", 0))
            kg_edges = int(m.get("kg_edges", 0))
            mem_records = int(m.get("memory_records", 0))
            score = float(m.get("last_self_test_score", 0.0))
            trend = str(m.get("growth_trend", "stable"))

            self._growth_labels["facts"].config(
                text=f"{facts_total} (+{facts_7d} 7d)", fg=self.TEXT)
            self._growth_labels["kg"].config(
                text=f"{kg_nodes} nodes / {kg_edges} edges", fg=self.TEXT)
            self._growth_labels["memory"].config(
                text=f"{mem_records} records", fg=self.TEXT)

            # Learning missions (API now pre-filters to active only)
            missions = m.get("active_missions", [])
            if isinstance(missions, list):
                missions = [mi for mi in missions if isinstance(mi, dict) and mi.get("status", "") not in ("completed", "failed", "cancelled", "exhausted")]
            mission_count = int(m.get("mission_count", len(missions) if isinstance(missions, list) else 0))
            if mission_count > 0 and isinstance(missions, list) and missions:
                topics = [str(mi.get("topic", "?"))[:20] for mi in missions[:3]]
                self._growth_labels["missions"].config(
                    text=f"{mission_count} active: {', '.join(topics)}", fg=self.ACCENT)
            elif mission_count > 0:
                self._growth_labels["missions"].config(
                    text=f"{mission_count} active", fg=self.ACCENT)
            else:
                self._growth_labels["missions"].config(
                    text="None active", fg=self.MUTED)

            score_pct = round(score * 100)
            score_color = self.ACCENT if score_pct >= 70 else "#eab308" if score_pct >= 50 else self.WARN
            self._growth_labels["score"].config(
                text=f"{score_pct}%", fg=score_color)

            trend_symbol = "\u25B2" if trend == "increasing" else "\u25BC" if trend == "declining" else "\u25C6"
            trend_color = "#22c55e" if trend == "increasing" else self.WARN if trend == "declining" else "#eab308"
            self._growth_labels["trend"].config(
                text=f"{trend_symbol} {trend}", fg=trend_color)
        except (TypeError, ValueError, KeyError):
            logger.debug("Failed to parse growth dashboard data; resetting labels")
            for lbl in self._growth_labels.values():
                lbl.config(text="--", fg=self.MUTED)

    def _update_activity_events(self, events: list[dict[str, Any]]) -> None:
        """Display new activity events in the conversation output (deduped by event_id)."""
        _CAT_ROLE = {
            "error": "error",
            "security": "error",
        }
        new_events = []
        for evt in events:
            eid = str(evt.get("event_id", ""))
            if eid and eid in self._seen_event_ids:
                continue
            if eid:
                self._seen_event_ids[eid] = None
            new_events.append(evt)
        # Cap seen dict to prevent unbounded growth (keeps most recent 400)
        if len(self._seen_event_ids) > 500:
            keys = list(self._seen_event_ids.keys())
            self._seen_event_ids = dict.fromkeys(keys[-400:])
        if not new_events:
            return
        for evt in reversed(new_events):  # Oldest first
            ts_raw = str(evt.get("timestamp", ""))
            ts_short = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
            cat = str(evt.get("category", ""))
            summary = str(evt.get("summary", ""))
            role = _CAT_ROLE.get(cat, "system")
            self._log(f"\u26a1 [{ts_short}] [{cat.upper()}] {summary}", role=role)

    def _update_intelligence_label(self, intel_data: dict[str, Any] | None) -> None:
        """Update the intelligence score label from /health response data."""
        if intel_data is None:
            self.intel_var.set("")
            self.intel_label.config(fg=self.MUTED)
            return
        try:
            score = float(intel_data.get("score", 0.0))
            regression = bool(intel_data.get("regression", False))
            score_pct = round(score * 100)
            if regression:
                self.intel_var.set(f"Intel: {score_pct}% REGRESSION")
                self.intel_label.config(fg=self.WARN)
            elif score_pct >= 70:
                self.intel_var.set(f"Intel: {score_pct}%")
                self.intel_label.config(fg=self.ACCENT)
            elif score_pct >= 50:
                self.intel_var.set(f"Intel: {score_pct}%")
                self.intel_label.config(fg="#eab308")  # yellow/warn
            else:
                self.intel_var.set(f"Intel: {score_pct}%")
                self.intel_label.config(fg=self.WARN)
        except (TypeError, ValueError):
            logger.debug("Failed to parse intelligence score data; clearing display")
            self.intel_var.set("")
            self.intel_label.config(fg=self.MUTED)

    def _set_state(self, state: str) -> None:
        """Update the visual state machine: idle | listening | processing | error."""
        if state not in ("idle", "listening", "processing", "error"):
            state = "idle"
        self._widget_state = state
        # Show/hide cancel button based on state
        cancel_btn = getattr(self, "_cancel_btn", None)
        if cancel_btn is not None:
            try:
                if state == "processing":
                    cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
                else:
                    cancel_btn.pack_forget()
                    self._cancel_event.clear()
            except tk.TclError:
                logger.debug("Failed to update cancel button visibility")
        self._refresh_status_view()

    def _set_state_async(self, state: str) -> None:
        """Schedule state change on the main tkinter thread."""
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._set_state, state)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Failed to schedule state change to %r (widget may be destroyed)", state)

    def _set_error_briefly(self) -> None:
        """Set error state for 2 seconds, then revert to idle."""
        self._set_state("error")
        # Cancel any previous error-clear timer
        if self._error_clear_id is not None:
            try:
                self.after_cancel(self._error_clear_id)
            except (tk.TclError, RuntimeError):  # Widget may be destroyed
                logger.debug("Failed to cancel previous error-clear timer")
        self._error_clear_id = self.after(2000, self._set_state, "idle")

    def _set_error_briefly_async(self) -> None:
        """Schedule brief error state on the main tkinter thread."""
        if self.stop_event.is_set():
            return
        try:
            self.after(0, self._set_error_briefly)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Failed to schedule error-briefly state (widget may be destroyed)")

    def _refresh_status_view(self) -> None:
        state = self._widget_state
        if state == "listening":
            self.status_var.set("LISTENING...")
            self.status_label.config(fg=self.ACCENT_2)
        elif state == "processing":
            label = "ONLINE - Processing..." if self.online else "OFFLINE - Processing..."
            self.status_var.set(label)
            self.status_label.config(fg="#ff9f43")
        elif state == "error":
            self.status_var.set("ERROR")
            self.status_label.config(fg=self.WARN)
        elif self.online:
            self.status_var.set("ONLINE - Idle")
            self.status_label.config(fg=self.ACCENT)
        else:
            self.status_var.set("OFFLINE")
            self.status_label.config(fg="#a5b4fc")


def run_desktop_widget() -> None:
    app = JarvisDesktopWidget(_repo_root())
    app.mainloop()
