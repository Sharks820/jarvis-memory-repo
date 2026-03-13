from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import tkinter as tk
from tkinter import messagebox

from jarvis_engine._constants import (
    DEFAULT_API_PORT as _DEFAULT_PORT,
    DEFAULT_CLOUD_MODEL,
    DEFAULT_LOCAL_MODEL,
    FAST_LOCAL_MODEL,
)
from jarvis_engine._shared import env_int as _env_int

_SECONDS_PER_HOUR = 3600
_MS_PER_SECOND = 1000
from jarvis_engine.desktop.controller import (
    DesktopInteractionController,
    DesktopWidgetState,
)
from jarvis_engine.desktop.conversation import ConversationMixin
from jarvis_engine.desktop.orb import OrbAnimationMixin
from jarvis_engine.desktop.tray import TrayMixin
from jarvis_engine.desktop.helpers import (  # noqa: F401 -- re-exported for tests
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
    _powershell_executable,
    _load_mobile_api_cfg,
    _load_widget_cfg,
    _mobile_api_cfg_path,
    _repo_root,
    _save_widget_cfg,
    _safe_urlopen,
    _security_dir,
    _show_toast,
    _signed_headers,
    _snap_to_edge,
    _taskkill_executable,
    _validated_widget_request_url,
    _voice_dictate_once,
    _widget_cfg_path,
)

logger = logging.getLogger(__name__)

_REMOTE_WIDGET_ERRORS = (HTTPError, URLError, RuntimeError, TimeoutError)

# Catch-all for best-effort HTTP/JSON operations in the widget.
# Covers network errors, JSON parse failures, and unexpected key/type issues.
_WIDGET_IO_ERRORS = (
    HTTPError, URLError, OSError, TimeoutError,
    json.JSONDecodeError, KeyError, ValueError, RuntimeError,
)

# Catch-all for voice/audio subsystem failures in the widget.
_WIDGET_VOICE_ERRORS = (
    OSError, RuntimeError, ValueError, TimeoutError, ImportError,
)


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
    PANEL_DEFAULT_WIDTH = 470
    PANEL_DEFAULT_HEIGHT = 820
    PANEL_MIN_WIDTH = 340
    PANEL_MIN_HEIGHT = 480

    # Model rotation: (alias, display_name, best_use_title, accent_color)
    # "auto" lets IntentClassifier decide; others override the model choice.
    # Immutable tuple-of-tuples to prevent accidental mutation of shared state.
    MODEL_ROTATION: tuple[tuple[str, str, str, str], ...] = (
        ("auto", "Auto", "Smart Router", "#12c9b1"),
        (FAST_LOCAL_MODEL, "Qwen 3.5 4B", "Fast Local · Everyday Desktop", "#22c55e"),
        (DEFAULT_LOCAL_MODEL, "Qwen 3.5 9B", "Deep Local · Reasoning & Privacy", "#38bdf8"),
        ("qwen3-coder:30b", "Qwen Coder 30B", "Heavy Local · Code Specialist", "#0ea5e9"),
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
        self._l_badge_bg: int | None = None
        self._l_badge_text: int | None = None
        self._l_particles: list[int] = []
        self._orb_sweep: int | None = None
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._launcher_dragged = False
        self._launcher_attention_until: float = 0.0
        self._orb_after_id: str | None = None
        self._launcher_after_id: str | None = None
        self._live_capsule_after_id: str | None = None
        self._prev_svc_running: dict[str, bool] = {}  # Track service state for crash detection
        self._widget_state: str = DesktopWidgetState.IDLE.value
        self._thinking_after_id: str | None = None  # after() id for dot animation
        self._thinking_dots: int = 3
        self._thinking_start_time: float = 0.0  # When thinking started (time.time())
        self._processing_timeout_id: str | None = None  # Safety timeout for stuck processing
        self._controller: DesktopInteractionController | None = None
        self._cmd_generation: int = 0  # Generation counter for race condition prevention
        self._cancel_event = threading.Event()
        self._hotword_active = threading.Event()
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
        self._last_diag_poll_at: float = 0.0
        self._ensure_controller()

        self.title("Jarvis Unlimited")
        self._fit_panel_to_screen()
        self._compact_layout = self._use_compact_layout(self.winfo_screenheight())
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
        self._animate_live_capsule()
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
            from jarvis_engine.ops.process_manager import read_pid_file, kill_service
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
                    [_powershell_executable(), "-NoProfile", "-Command",
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
        live_capsule_after_id = getattr(self, "_live_capsule_after_id", None)
        if live_capsule_after_id is not None:
            try:
                self.after_cancel(live_capsule_after_id)
            except (tk.TclError, RuntimeError) as exc:
                logger.debug("Failed to cancel live capsule animation callback: %s", exc)
        # Wait briefly for background threads to finish
        for t in threading.enumerate():
            if t.daemon and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.0)
        if self.launcher_win is not None:
            try:
                self.launcher_win.destroy()
            except (tk.TclError, RuntimeError, OSError) as exc:
                logger.debug("Failed to destroy launcher window during shutdown: %s", exc)
        try:
            self.destroy()
        except (tk.TclError, RuntimeError, OSError) as exc:
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

    def _panel_visible(self) -> bool:
        """Return whether the main widget panel is currently viewable."""
        try:
            return self.state() not in {"withdrawn", "iconic"} and bool(self.winfo_viewable())
        except (tk.TclError, RuntimeError):
            return False

    @classmethod
    def _panel_size_for_screen(cls, screen_w: int, screen_h: int) -> tuple[int, int]:
        """Return a widget size that always fits on the active screen."""
        max_w = max(cls.PANEL_MIN_WIDTH, screen_w - 32)
        max_h = max(cls.PANEL_MIN_HEIGHT, screen_h - 88)
        width = min(cls.PANEL_DEFAULT_WIDTH, max_w)
        height = min(cls.PANEL_DEFAULT_HEIGHT, max_h)
        return max(cls.PANEL_MIN_WIDTH, width), max(cls.PANEL_MIN_HEIGHT, height)

    @classmethod
    def _use_compact_layout(cls, screen_h: int) -> bool:
        """Return whether the desktop widget should tighten vertical layout."""
        return screen_h < 900

    @staticmethod
    def _command_box_height(compact_layout: bool) -> int:
        """Return the command input height for the current layout density."""
        return 3 if compact_layout else 5

    @staticmethod
    def _collapse_live_details_by_default(compact_layout: bool) -> bool:
        """Return whether the live-detail block should start collapsed."""
        return compact_layout

    @staticmethod
    def _collapse_system_snapshot_by_default(compact_layout: bool) -> bool:
        """Return whether the system snapshot block should start collapsed."""
        return compact_layout

    @staticmethod
    def _flag_grid_columns(compact_layout: bool) -> int:
        """Return the number of columns for the command posture controls."""
        return 3 if compact_layout else 6

    def _fit_panel_to_screen(self) -> tuple[int, int]:
        """Clamp widget size and position so the panel stays usable on small screens."""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width, height = self._panel_size_for_screen(screen_w, screen_h)
        self.maxsize(max(self.PANEL_MIN_WIDTH, screen_w - 12), max(self.PANEL_MIN_HEIGHT, screen_h - 48))
        self.minsize(min(width, 420), min(height, 560))
        x = self.cfg.panel_x if self.cfg.panel_x is not None else 40
        y = self.cfg.panel_y if self.cfg.panel_y is not None else 60
        x = min(max(x, 0), max(0, screen_w - width))
        y = min(max(y, 0), max(0, screen_h - 40 - height))
        self.geometry(f"{width}x{height}+{x}+{y}")
        return width, height

    def _launcher_visible(self) -> bool:
        """Return whether the floating launcher orb is currently visible."""
        launcher = getattr(self, "launcher_win", None)
        if launcher is None:
            return False
        try:
            return launcher.state() != "withdrawn" and bool(launcher.winfo_viewable())
        except (tk.TclError, RuntimeError):
            return False

    def _mark_launcher_attention(self, events: list[dict[str, Any]]) -> None:
        """Extend launcher attention pulse when important hidden-panel events arrive."""
        if not events:
            return
        duration = 0.0
        for event in events:
            category = str(event.get("category", "")).strip().lower()
            if category in {"error", "security"}:
                duration = max(duration, 12.0)
            elif category in {"voice", "voice_pipeline", "mission", "learning", "command"}:
                duration = max(duration, 6.0)
        if duration <= 0:
            return
        self._launcher_attention_until = max(
            getattr(self, "_launcher_attention_until", 0.0),
            time.monotonic() + duration,
        )

    def _launcher_attention_active(self) -> bool:
        """Return whether the launcher should currently pulse for attention."""
        return time.monotonic() < getattr(self, "_launcher_attention_until", 0.0)

    def _show_panel(self) -> None:
        self._fit_panel_to_screen()
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
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.debug("Failed to save widget position to config: %s", exc)

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=self.BG, bd=0)
        shell.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._build_toolbar(shell)

        body = tk.Frame(shell, bg=self.PANEL, highlightbackground=self.EDGE, highlightthickness=1)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self._build_command_area(body)
        self._build_status_bar(body)
        self._build_continuity_rail(body)
        self._build_diagnostics_section(body)
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

        self._build_live_capsule(header)
        self._render_live_snapshot()

    def _build_live_capsule(self, header: tk.Frame) -> None:
        """Build the live desktop capsule that reflects controller-owned truth."""
        capsule = tk.Frame(header, bg="#0a1424", highlightbackground="#1f3558", highlightthickness=1)
        capsule.pack(fill=tk.X, padx=10, pady=(0, 10))
        self._live_capsule = capsule

        self._build_live_capsule_top_row(capsule)
        self._build_live_capsule_context(capsule)
        self._build_live_capsule_activity(capsule)
        self._build_live_capsule_detail(capsule)

    def _build_live_capsule_top_row(self, capsule: tk.Frame) -> None:
        """Build the top row with mode, health, mission, and toggle controls."""
        top_row = tk.Frame(capsule, bg="#0a1424")
        top_row.pack(fill=tk.X, padx=10, pady=(8, 2))
        self._live_capsule_top = top_row
        self._live_mode_var = tk.StringVar(value="READY ON DESKTOP")
        self._live_mode_label = tk.Label(
            top_row,
            textvariable=self._live_mode_var,
            bg="#0a1424",
            fg=self.ACCENT,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        )
        self._live_mode_label.pack(side=tk.LEFT)
        self._health_chip_var = tk.StringVar(value="Health --")
        self._health_chip_btn = tk.Button(
            top_row,
            textvariable=self._health_chip_var,
            bg="#1f2937",
            fg="#d1d5db",
            activebackground="#1f2937",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            padx=8,
            pady=3,
            command=self._toggle_diagnostics_section,
        )
        self._health_chip_btn.pack(side=tk.RIGHT, padx=(0, 6))
        self._mission_chip_var = tk.StringVar(value="No live missions")
        self._mission_chip_label = tk.Label(
            top_row,
            textvariable=self._mission_chip_var,
            bg="#12304d",
            fg="#c7e6ff",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self._mission_chip_label.pack(side=tk.RIGHT, padx=(0, 6))
        self._live_detail_collapsed = tk.BooleanVar(
            value=self._collapse_live_details_by_default(self._compact_layout)
        )
        self._live_detail_toggle_btn = tk.Button(
            top_row,
            text="Expand Live View" if self._live_detail_collapsed.get() else "Collapse Live View",
            bg="#0a1424",
            fg=self.MUTED,
            activebackground="#0a1424",
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            command=self._toggle_live_detail_section,
        )
        self._live_detail_toggle_btn.pack(side=tk.RIGHT, padx=(0, 6))

    def _build_live_capsule_context(self, capsule: tk.Frame) -> None:
        """Build the always-visible context label inside the live capsule."""
        self._live_context_var = tk.StringVar(
            value="Voice, missions, and desktop context stay synchronized here."
        )
        self._live_context_label = tk.Label(
            capsule,
            textvariable=self._live_context_var,
            bg="#0a1424",
            fg="#d8e7ff",
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            wraplength=330 if self._compact_layout else 385,
            anchor="w",
        )
        self._live_context_label.pack(fill=tk.X, padx=10, pady=(4, 2))

    def _build_live_capsule_activity(self, capsule: tk.Frame) -> None:
        """Build the always-visible activity row with signal, learned, and intel chips."""
        activity_row = tk.Frame(capsule, bg="#0a1424")
        activity_row.pack(fill=tk.X, padx=10, pady=(0, 4))
        self._activity_chip_var = tk.StringVar(value="No fresh signals yet")
        self._activity_chip_label = tk.Label(
            activity_row,
            textvariable=self._activity_chip_var,
            bg="#132238",
            fg=self.MUTED,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
            anchor="w",
        )
        self._activity_chip_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._learned_count = 0
        self._learned_chip_var = tk.StringVar(value="")
        self._learned_chip_label = tk.Label(
            activity_row,
            textvariable=self._learned_chip_var,
            bg="#1a3a1a",
            fg="#7dd3a0",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        # Hidden until first learning event
        self._intel_chip_var = tk.StringVar(value="Intel --")
        self._intel_chip_label = tk.Label(
            activity_row,
            textvariable=self._intel_chip_var,
            bg="#123624",
            fg="#b7f0cf",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self._intel_chip_label.pack(side=tk.RIGHT)

    def _build_live_capsule_detail(self, capsule: tk.Frame) -> None:
        """Build the collapsible detail section with mission progress and ops rail."""
        self._live_detail_body = tk.Frame(capsule, bg="#0a1424")

        progress_row = tk.Frame(self._live_detail_body, bg="#0a1424")
        progress_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._mission_progress_var = tk.StringVar(value="Continuity primed")
        self._mission_progress_label = tk.Label(
            progress_row,
            textvariable=self._mission_progress_var,
            bg="#0a1424",
            fg="#8fb4d8",
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        self._mission_progress_label.pack(side=tk.LEFT)
        self._mission_progress_pct_var = tk.StringVar(value="0%")
        self._mission_progress_pct_label = tk.Label(
            progress_row,
            textvariable=self._mission_progress_pct_var,
            bg="#0a1424",
            fg="#8fb4d8",
            font=("Segoe UI", 8, "bold"),
            anchor="e",
        )
        self._mission_progress_pct_label.pack(side=tk.RIGHT)

        self._mission_progress_canvas = tk.Canvas(
            self._live_detail_body,
            width=390,
            height=16,
            bg="#0a1424",
            highlightthickness=0,
            bd=0,
        )
        self._mission_progress_canvas.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._mission_progress_track = self._mission_progress_canvas.create_rectangle(
            1,
            3,
            389,
            13,
            fill="#13243d",
            outline="#214162",
            width=1,
        )
        self._mission_progress_fill = self._mission_progress_canvas.create_rectangle(
            2,
            4,
            2,
            12,
            fill="#34d3ba",
            outline="",
        )
        self._mission_progress_glow = self._mission_progress_canvas.create_rectangle(
            2,
            4,
            2,
            12,
            fill="#7dd3fc",
            outline="",
            stipple="gray50",
        )

        self._build_live_ops_rail(self._live_detail_body)
        self._apply_live_detail_state()

    def _build_live_ops_rail(self, capsule: tk.Frame) -> None:
        """Build the controller-backed posture chips inside the live capsule."""
        rail = tk.Frame(capsule, bg="#0a1424")
        rail.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._live_ops_rail = rail

        top_row = tk.Frame(rail, bg="#0a1424")
        top_row.pack(fill=tk.X, pady=(0, 4))
        self._route_chip_var = tk.StringVar(value="Auto Router")
        self._route_chip_label = tk.Label(
            top_row,
            textvariable=self._route_chip_var,
            bg="#123624",
            fg="#b7f0cf",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self._route_chip_label.pack(side=tk.LEFT)
        self._control_chip_var = tk.StringVar(value="Advisory only")
        self._control_chip_label = tk.Label(
            top_row,
            textvariable=self._control_chip_var,
            bg="#1b2940",
            fg="#d7e6ff",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self._control_chip_label.pack(side=tk.LEFT, padx=(6, 0))

        bottom_row = tk.Frame(rail, bg="#0a1424")
        bottom_row.pack(fill=tk.X)
        self._approval_chip_var = tk.StringVar(value="Approval required")
        self._approval_chip_label = tk.Label(
            bottom_row,
            textvariable=self._approval_chip_var,
            bg="#3a2a12",
            fg="#fde68a",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self._approval_chip_label.pack(side=tk.LEFT)
        self._voice_chip_var = tk.StringVar(value="Push to talk")
        self._voice_chip_label = tk.Label(
            bottom_row,
            textvariable=self._voice_chip_var,
            bg="#172554",
            fg="#bfdbfe",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self._voice_chip_label.pack(side=tk.LEFT, padx=(6, 0))

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
        self._session_section = sec

        self.base_var = tk.StringVar(value=self.cfg.base_url)
        self.token_var = tk.StringVar(value=self.cfg.token)
        self.key_var = tk.StringVar(value=self.cfg.signing_key)
        self.device_var = tk.StringVar(value=self.cfg.device_id)
        self.master_var = tk.StringVar(value=self.cfg.master_password)

        session_header = tk.Frame(sec, bg=self.PANEL)
        session_header.pack(fill=tk.X, padx=6, pady=(4, 0))
        self._session_toggle_btn = tk.Button(
            session_header,
            text="Show Session",
            bg=self.PANEL,
            fg=self.MUTED,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            command=self._toggle_session_section,
        )
        self._session_toggle_btn.pack(side=tk.RIGHT)
        self._session_body = tk.Frame(sec, bg=self.PANEL)
        self._session_collapsed = tk.BooleanVar(value=True)

        self._entry(self._session_body, "Base URL", self.base_var)
        self._entry(self._session_body, "Master password", self.master_var, show="*")

        # Advanced fields (hidden by default)
        self._adv_visible = tk.BooleanVar(value=False)
        adv_toggle = tk.Frame(self._session_body, bg=self.PANEL)
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
        self._adv_frame = tk.Frame(self._session_body, bg=self.PANEL)
        self._entry(self._adv_frame, "Bearer token", self.token_var)
        self._entry(self._adv_frame, "Signing key", self.key_var)
        self._entry(self._adv_frame, "Device ID", self.device_var)
        # Advanced frame hidden by default (not packed)

        self._sec_buttons = tk.Frame(self._session_body, bg=self.PANEL)
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
        self._apply_session_section_state()

    def _build_command_input(self, body: tk.Frame) -> None:
        """Build the command text area with model indicator row."""
        cmd_block = tk.Frame(body, bg=self.PANEL)
        cmd_block.pack(fill=tk.X, padx=10)
        tk.Label(cmd_block, text="Command", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.command_text = tk.Text(
            cmd_block,
            height=self._command_box_height(self._compact_layout),
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
        toggles = [
            self._check(flags, "Allow PC Actions", self.execute_var, cmd=self._on_posture_toggle),
            self._check(flags, "Auto-Approve", self.priv_var, cmd=self._on_posture_toggle),
            self._check(flags, "Speak", self.speak_var, cmd=self._on_posture_toggle),
            self._check(flags, "Auto Send", self.auto_send_var, cmd=self._on_posture_toggle),
            self._check(flags, "Wake Word", self.hotword_var, cmd=self._on_hotword_toggle),
            self._check(flags, "Notifications", self.notify_var, cmd=self._on_posture_toggle),
        ]
        if self._compact_layout:
            columns = self._flag_grid_columns(True)
            for column in range(columns):
                flags.grid_columnconfigure(column, weight=1)
            for index, toggle in enumerate(toggles):
                toggle.grid(
                    row=index // columns,
                    column=index % columns,
                    sticky="w",
                    padx=(0, 8),
                    pady=(0, 2),
                )
        else:
            for index, toggle in enumerate(toggles):
                toggle.pack(side=tk.LEFT, padx=(0, 10 if index < len(toggles) - 1 else 0))
        self._push_session_snapshot()

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
        status_shell = tk.LabelFrame(body, text="System Snapshot", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        status_shell.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._status_section = status_shell
        status_header = tk.Frame(status_shell, bg=self.PANEL)
        status_header.pack(fill=tk.X, padx=6, pady=(4, 0))
        self._status_collapsed = tk.BooleanVar(
            value=self._collapse_system_snapshot_by_default(self._compact_layout)
        )
        self._status_toggle_btn = tk.Button(
            status_header,
            text="Show Snapshot" if self._status_collapsed.get() else "Hide Snapshot",
            bg=self.PANEL,
            fg=self.MUTED,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            command=self._toggle_status_section,
        )
        self._status_toggle_btn.pack(side=tk.RIGHT)
        self._status_body = tk.Frame(status_shell, bg=self.PANEL)

        # Running Services section
        svc_frame = tk.LabelFrame(self._status_body, text="Running Services", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        svc_frame.pack(fill=tk.X, padx=6, pady=(4, 0))
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
        growth_frame = tk.LabelFrame(self._status_body, text="Brain Growth", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        growth_frame.pack(fill=tk.X, padx=6, pady=(8, 8))
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
        self._apply_status_section_state()

    def _build_continuity_rail(self, body: tk.Frame) -> None:
        """Build the continuity rail backed by conversation-state truth."""
        rail = tk.LabelFrame(body, text="Continuity Rail", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        rail.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._continuity_section = rail

        header = tk.Frame(rail, bg=self.PANEL)
        header.pack(fill=tk.X, padx=6, pady=(4, 0))
        self._continuity_counts_var = tk.StringVar(value="0 anchors · 0 goals · 0 decisions · 0 turns")
        self._continuity_counts_label = tk.Label(
            header,
            textvariable=self._continuity_counts_var,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        self._continuity_counts_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._continuity_collapsed = tk.BooleanVar(value=True)
        self._continuity_toggle_btn = tk.Button(
            header,
            text="Show Continuity" if self._continuity_collapsed.get() else "Hide Continuity",
            bg=self.PANEL,
            fg=self.MUTED,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            command=self._toggle_continuity_section,
        )
        self._continuity_toggle_btn.pack(side=tk.RIGHT)

        self._continuity_body = tk.Frame(rail, bg=self.PANEL)
        self._continuity_summary_var = tk.StringVar(
            value="Jarvis will surface key entities, unresolved goals, and prior decisions here."
        )
        self._continuity_summary_label = tk.Label(
            self._continuity_body,
            textvariable=self._continuity_summary_var,
            bg=self.PANEL,
            fg="#d7e6ff",
            font=("Segoe UI", 9),
            justify=tk.LEFT,
            wraplength=385,
            anchor="w",
        )
        self._continuity_summary_label.pack(fill=tk.X, padx=6, pady=(4, 6))

        self._continuity_anchor_var = tk.StringVar(value="No anchors yet")
        self._continuity_goal_var = tk.StringVar(value="No unresolved goals yet")
        self._continuity_decision_var = tk.StringVar(value="No prior decisions yet")
        for label_text, var_name in (
            ("Anchors", "_continuity_anchor_var"),
            ("Goals", "_continuity_goal_var"),
            ("Decisions", "_continuity_decision_var"),
        ):
            row = tk.Frame(self._continuity_body, bg=self.PANEL)
            row.pack(fill=tk.X, padx=6, pady=(0, 4))
            tk.Label(row, text=label_text, bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 8, "bold"), width=9, anchor="w").pack(side=tk.LEFT)
            tk.Label(
                row,
                textvariable=getattr(self, var_name),
                bg=self.PANEL,
                fg=self.TEXT,
                font=("Segoe UI", 8),
                justify=tk.LEFT,
                wraplength=320,
                anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._apply_continuity_section_state()

    def _build_diagnostics_section(self, body: tk.Frame) -> None:
        """Build a compact diagnostics drawer for health issues and repair actions."""
        panel = tk.LabelFrame(body, text="System Health", bg=self.PANEL, fg=self.MUTED, bd=1, relief=tk.GROOVE)
        panel.pack(fill=tk.X, padx=10, pady=(8, 0))
        self._diagnostics_section = panel

        header = tk.Frame(panel, bg=self.PANEL)
        header.pack(fill=tk.X, padx=6, pady=(4, 0))
        self._diagnostics_summary_var = tk.StringVar(value="Health status will appear here after the first quick scan.")
        self._diagnostics_summary_label = tk.Label(
            header,
            textvariable=self._diagnostics_summary_var,
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        self._diagnostics_summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._diagnostics_collapsed = tk.BooleanVar(value=True)
        self._diagnostics_toggle_btn = tk.Button(
            header,
            text="Show Health" if self._diagnostics_collapsed.get() else "Hide Health",
            bg=self.PANEL,
            fg=self.MUTED,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            command=self._toggle_diagnostics_section,
        )
        self._diagnostics_toggle_btn.pack(side=tk.RIGHT)

        self._diagnostics_body = tk.Frame(panel, bg=self.PANEL)
        self._diagnostics_detail_var = tk.StringVar(
            value="Quick diagnostics have not run yet. Use the health chip or Diagnose button to refresh."
        )
        self._diagnostics_detail_label = tk.Label(
            self._diagnostics_body,
            textvariable=self._diagnostics_detail_var,
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Segoe UI", 8),
            justify=tk.LEFT,
            wraplength=385,
            anchor="w",
        )
        self._diagnostics_detail_label.pack(fill=tk.X, padx=6, pady=(4, 6))
        self._diagnostics_issue_var = tk.StringVar(value="No quick-scan issues recorded yet.")
        self._diagnostics_issue_label = tk.Label(
            self._diagnostics_body,
            textvariable=self._diagnostics_issue_var,
            bg=self.PANEL,
            fg="#d7e6ff",
            font=("Segoe UI", 8),
            justify=tk.LEFT,
            wraplength=385,
            anchor="w",
        )
        self._diagnostics_issue_label.pack(fill=tk.X, padx=6, pady=(0, 6))
        actions = tk.Frame(self._diagnostics_body, bg=self.PANEL)
        actions.pack(fill=tk.X, padx=6, pady=(0, 8))
        self._diagnostics_refresh_btn = self._btn(actions, "Refresh Health", self._refresh_diagnostics_async, "#35517a")
        self._diagnostics_refresh_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._diagnostics_repair_btn = self._btn(actions, "Diagnose & Repair", self._diagnose_repair_async, "#1f5f88")
        self._diagnostics_repair_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._apply_diagnostics_section_state()

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

    def _apply_session_section_state(self) -> None:
        """Expand or collapse the Secure Session body."""
        collapsed = bool(self._session_collapsed.get())
        if collapsed:
            self._session_body.pack_forget()
            self._session_toggle_btn.config(text="Show Session")
        else:
            self._session_body.pack(fill=tk.X, padx=0, pady=(0, 0))
            self._session_toggle_btn.config(text="Hide Session")

    def _toggle_session_section(self) -> None:
        """Toggle the compact Secure Session section."""
        self._session_collapsed.set(not bool(self._session_collapsed.get()))
        self._apply_session_section_state()

    def _apply_live_detail_state(self) -> None:
        """Expand or collapse the live-detail block."""
        collapsed = bool(self._live_detail_collapsed.get())
        if collapsed:
            self._live_detail_body.pack_forget()
            self._live_detail_toggle_btn.config(text="Expand Live View")
        else:
            self._live_detail_body.pack(fill=tk.X, padx=0, pady=(0, 0))
            self._live_detail_toggle_btn.config(text="Collapse Live View")

    def _toggle_live_detail_section(self) -> None:
        """Toggle the live-detail block."""
        self._live_detail_collapsed.set(not bool(self._live_detail_collapsed.get()))
        self._apply_live_detail_state()

    def _apply_status_section_state(self) -> None:
        """Expand or collapse the system snapshot block."""
        collapsed = bool(self._status_collapsed.get())
        if collapsed:
            self._status_body.pack_forget()
            self._status_toggle_btn.config(text="Show Snapshot")
        else:
            self._status_body.pack(fill=tk.X, padx=0, pady=(0, 0))
            self._status_toggle_btn.config(text="Hide Snapshot")

    def _toggle_status_section(self) -> None:
        """Toggle the system snapshot block."""
        self._status_collapsed.set(not bool(self._status_collapsed.get()))
        self._apply_status_section_state()

    def _apply_continuity_section_state(self) -> None:
        """Expand or collapse the continuity rail body."""
        collapsed = bool(self._continuity_collapsed.get())
        if collapsed:
            self._continuity_body.pack_forget()
            self._continuity_toggle_btn.config(text="Show Continuity")
        else:
            self._continuity_body.pack(fill=tk.X, padx=0, pady=(0, 0))
            self._continuity_toggle_btn.config(text="Hide Continuity")

    def _toggle_continuity_section(self) -> None:
        """Toggle the continuity rail body."""
        self._continuity_collapsed.set(not bool(self._continuity_collapsed.get()))
        self._apply_continuity_section_state()

    def _apply_diagnostics_section_state(self) -> None:
        """Expand or collapse the diagnostics drawer body."""
        collapsed = bool(self._diagnostics_collapsed.get())
        if collapsed:
            self._diagnostics_body.pack_forget()
            self._diagnostics_toggle_btn.config(text="Show Health")
        else:
            self._diagnostics_body.pack(fill=tk.X, padx=0, pady=(0, 0))
            self._diagnostics_toggle_btn.config(text="Hide Health")

    def _toggle_diagnostics_section(self) -> None:
        """Toggle the diagnostics drawer body."""
        self._diagnostics_collapsed.set(not bool(self._diagnostics_collapsed.get()))
        self._apply_diagnostics_section_state()

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
            command=cmd or "",
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
        state_bits = event.state if isinstance(event.state, int) else 0
        if state_bits & 0x0001:  # Shift key pressed
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
        if self._compact_layout:
            self._session_collapsed.set(True)
            self._apply_session_section_state()

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
                if self._compact_layout:
                    self.after(0, lambda: self._session_collapsed.set(True))
                    self.after(0, self._apply_session_section_state)
                trusted = bool(session.get("trusted_device", False))
                self._log_async(f"Bootstrap complete. trusted_device={trusted}", role="jarvis")
                self.after(0, self._show_welcome)
            except _REMOTE_WIDGET_ERRORS as exc:
                self._handle_transport_failure(
                    "Connect",
                    exc,
                    hints=(("Make sure the Mobile API is running and the Base URL is correct.", "error"),),
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as exc:
                self._handle_transport_failure(
                    "Connect",
                    exc,
                    hints=(("Make sure the Mobile API is running and the Base URL is correct.", "error"),),
                )

        self._thread(worker)

    def _diag_check_connection(self, cfg: WidgetConfig) -> bool:
        """Diagnostic step 1: check API connection. Returns False if offline."""
        self._log_async("[1/4] Checking API connection...", role="system")
        try:
            health_data = _http_json(cfg, "/health", method="GET")
            health_ok = bool(health_data.get("ok", False))
            self._log_async(f"  API: {'ONLINE' if health_ok else 'DEGRADED'}", role="jarvis")
            return True
        except _WIDGET_IO_ERRORS as exc:
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
        except _WIDGET_IO_ERRORS as exc:
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
        except _WIDGET_IO_ERRORS as exc:
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
            except _REMOTE_WIDGET_ERRORS as exc:
                self._handle_transport_failure(
                    "Diagnose",
                    exc,
                    hints=(
                        ("Make sure the Assistant and Mobile API are running.", "error"),
                        ("Start with: jarvis-engine daemon", "system"),
                    ),
                    notify_toast=("Jarvis", "Diagnose & Repair failed", "Error"),
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as exc:
                self._handle_transport_failure(
                    "Diagnose",
                    exc,
                    hints=(
                        ("Make sure the Assistant and Mobile API are running.", "error"),
                        ("Start with: jarvis-engine daemon", "system"),
                    ),
                    notify_toast=("Jarvis", "Diagnose & Repair failed", "Error"),
                )

        self._thread(worker)

    def _refresh_diagnostics_async(self) -> None:
        """Refresh quick diagnostics state for the health pulse/drawer."""
        cfg = self._current_cfg()

        def worker() -> None:
            data = self._fetch_diagnostics_status(cfg)
            if data is None:
                self._log_async("Quick health scan unavailable right now.", role="error")
                return
            raw_issues = data.get("issues")
            issue_list: list[dict[str, Any]]
            if isinstance(raw_issues, list):
                issue_list = [item for item in raw_issues if isinstance(item, dict)]
            else:
                issue_list = []
            score = data.get("score") if isinstance(data.get("score"), int) else None
            healthy = bool(data.get("healthy", False))
            self.after(
                0,
                lambda: self._ensure_controller().apply_diagnostics_snapshot(
                    score=score,
                    healthy=healthy,
                    issues=issue_list,
                    error=str(data.get("error", "")),
                ),
            )
            self.after(0, self._render_live_snapshot)
            summary = "healthy" if healthy else f"{len(issue_list)} issue(s) flagged"
            self._log_async(f"Quick health scan refreshed: {summary}.", role="system")

        self._thread(worker)

    def _thread(self, fn: Callable[..., Any]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _ensure_controller(self) -> DesktopInteractionController:
        """Return the authoritative desktop interaction controller."""
        controller = getattr(self, "_controller", None)
        if not isinstance(controller, DesktopInteractionController):
            controller = DesktopInteractionController(
                on_state_change=lambda state: JarvisDesktopWidget._apply_controller_state(self, state),
            )
            self._controller = controller
            self._cancel_event = controller.cancel_event
            self._hotword_active = controller.hotword_event
            self._cmd_generation = controller.command_generation
        return controller

    def _render_live_snapshot(self) -> None:
        """Render the controller-owned live desktop snapshot into the UI."""
        snapshot = self._ensure_controller().snapshot()
        mode_text, mode_color = self._snapshot_mode_text(snapshot)
        self.status_var.set(mode_text)
        self.status_label.config(fg=mode_color)

        intel_text, intel_color = self._snapshot_intelligence_text(snapshot)
        self.intel_var.set(intel_text)
        self.intel_label.config(fg=intel_color)

        live_mode_var = getattr(self, "_live_mode_var", None)
        live_mode_label = getattr(self, "_live_mode_label", None)
        if live_mode_var is not None:
            live_mode_var.set(mode_text.upper())
        if live_mode_label is not None:
            live_mode_label.config(fg=mode_color)

        mission_chip_var = getattr(self, "_mission_chip_var", None)
        mission_chip_label = getattr(self, "_mission_chip_label", None)
        mission_text, mission_bg, mission_fg = self._snapshot_mission_chip(snapshot)
        if mission_chip_var is not None:
            mission_chip_var.set(mission_text)
        if mission_chip_label is not None:
            mission_chip_label.config(bg=mission_bg, fg=mission_fg)

        context_var = getattr(self, "_live_context_var", None)
        context_label = getattr(self, "_live_context_label", None)
        context_text, context_fg = self._snapshot_context_line(snapshot)
        if context_var is not None:
            context_var.set(context_text)
        if context_label is not None:
            context_label.config(fg=context_fg)

        conversation_status_var = getattr(self, "_conversation_status_var", None)
        conversation_status_label = getattr(self, "_conversation_status_label", None)
        conversation_status_text, conversation_status_bg, conversation_status_fg = self._snapshot_conversation_status(snapshot)
        if conversation_status_var is not None:
            conversation_status_var.set(conversation_status_text)
        if conversation_status_label is not None:
            conversation_status_label.config(bg=conversation_status_bg, fg=conversation_status_fg)

        activity_var = getattr(self, "_activity_chip_var", None)
        activity_label = getattr(self, "_activity_chip_label", None)
        activity_text, activity_bg, activity_fg = self._snapshot_activity_chip(snapshot)
        if activity_var is not None:
            activity_var.set(activity_text)
        if activity_label is not None:
            activity_label.config(bg=activity_bg, fg=activity_fg)

        intel_chip_var = getattr(self, "_intel_chip_var", None)
        intel_chip_label = getattr(self, "_intel_chip_label", None)
        intel_chip_text, intel_chip_bg, intel_chip_fg = self._snapshot_intel_chip(snapshot)
        if intel_chip_var is not None:
            intel_chip_var.set(intel_chip_text)
        if intel_chip_label is not None:
            intel_chip_label.config(bg=intel_chip_bg, fg=intel_chip_fg)

        health_chip_var = getattr(self, "_health_chip_var", None)
        health_chip_btn = getattr(self, "_health_chip_btn", None)
        health_chip_text, health_chip_bg, health_chip_fg = self._snapshot_health_chip(snapshot)
        if health_chip_var is not None:
            health_chip_var.set(health_chip_text)
        if health_chip_btn is not None:
            health_chip_btn.config(
                bg=health_chip_bg,
                fg=health_chip_fg,
                activebackground=health_chip_bg,
                activeforeground="#ffffff",
            )

        self._render_session_snapshot(snapshot)
        self._render_mission_progress(snapshot)
        self._render_growth_snapshot(snapshot)
        self._render_continuity_snapshot(snapshot)
        self._render_diagnostics_snapshot(snapshot)

    def _apply_controller_state(self, state: DesktopWidgetState) -> None:
        """Render a controller-owned state transition into widget UI state."""
        self._widget_state = state.value
        cancel_btn = getattr(self, "_cancel_btn", None)
        if cancel_btn is not None:
            try:
                if state is DesktopWidgetState.PROCESSING:
                    cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
                else:
                    cancel_btn.pack_forget()
            except tk.TclError:
                logger.debug("Failed to update cancel button visibility")
        self._refresh_status_view()

    def _snapshot_mode_text(self, snapshot: Any) -> tuple[str, str]:
        """Build headline status text and tone from the desktop snapshot."""
        if snapshot.state is DesktopWidgetState.LISTENING:
            return "Listening now", self.ACCENT_2
        if snapshot.state is DesktopWidgetState.PROCESSING:
            prefix = "Online" if snapshot.online else "Offline"
            return f"{prefix} · Thinking", "#ff9f43"
        if snapshot.state is DesktopWidgetState.ERROR:
            return "Recovery mode", self.WARN
        if snapshot.online:
            return "Ready on desktop", self.ACCENT
        return "Offline", "#a5b4fc"

    def _snapshot_intelligence_text(self, snapshot: Any) -> tuple[str, str]:
        """Return the compact intelligence readout shown in the toolbar."""
        if snapshot.intelligence_score_pct is None:
            return "", self.MUTED
        if snapshot.intelligence_regression:
            return f"Intel: {snapshot.intelligence_score_pct}% · Below Target", self.WARN
        if snapshot.intelligence_score_pct >= 70:
            return f"Intel: {snapshot.intelligence_score_pct}%", self.ACCENT
        if snapshot.intelligence_score_pct >= 50:
            return f"Intel: {snapshot.intelligence_score_pct}%", "#eab308"
        return f"Intel: {snapshot.intelligence_score_pct}%", self.WARN

    def _snapshot_mission_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return mission-chip text and palette for the live capsule."""
        mission = snapshot.mission
        if mission.current_topic:
            topic = mission.current_topic[:22]
            progress = f" {mission.progress_pct}%" if mission.progress_pct > 0 else ""
            return f"{topic}{progress}", "#163857", "#d7ebff"
        if mission.count > 0:
            return f"{mission.count} live mission{'s' if mission.count != 1 else ''}", "#163857", "#d7ebff"
        return "No live missions", "#1a2030", self.MUTED

    def _snapshot_context_line(self, snapshot: Any) -> tuple[str, str]:
        """Return the main descriptive line inside the live capsule."""
        mission = snapshot.mission
        activity = snapshot.activity
        if snapshot.state is DesktopWidgetState.LISTENING:
            return "Wake word or dictation is active. Jarvis is waiting for your voice input.", "#cae1ff"
        if snapshot.state is DesktopWidgetState.PROCESSING:
            if mission.current_step:
                return f"Working on {mission.current_topic or 'active mission'}: {mission.current_step}", "#ffe2b6"
            return "Reasoning through the current request with continuity and mission context intact.", "#ffe2b6"
        if mission.current_step:
            topic = mission.current_topic or "Current mission"
            return f"{topic}: {mission.current_step}", "#d8e7ff"
        if mission.count > 0 and mission.topics:
            return f"Live mission focus: {', '.join(mission.topics)}", "#d8e7ff"
        if activity.summary:
            return activity.summary, "#d8e7ff"
        if snapshot.online:
            return "Desktop assistant is live. Voice, activity, and learning surfaces are synchronized.", "#d8e7ff"
        return "Desktop services are unreachable. The launcher stays available while the brain reconnects.", "#c4d1e9"

    def _snapshot_activity_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return activity-chip content and palette based on recent signals."""
        activity = snapshot.activity
        if not activity.summary:
            return "No fresh signals yet", "#132238", self.MUTED
        prefix = activity.category.upper() if activity.category else "EVENT"
        ts = f"{activity.timestamp} · " if activity.timestamp else ""
        text = f"{ts}{prefix}: {activity.summary[:72]}"
        if activity.category in {"error", "security"}:
            return text, "#33181b", "#fecaca"
        if activity.category in {"voice", "voice_pipeline"}:
            return text, "#1c1b3a", "#d7ccff"
        return text, "#132238", "#d7e6ff"

    def _snapshot_conversation_status(self, snapshot: Any) -> tuple[str, str, str]:
        """Return the inline execution-status strip shown above the transcript."""
        route_label = snapshot.session.route_label or "Auto Router"
        mission = snapshot.mission
        activity = snapshot.activity
        if snapshot.state is DesktopWidgetState.LISTENING:
            return f"Listening now via {route_label}. Speak your request.", "#10284a", "#cae1ff"
        if snapshot.state is DesktopWidgetState.PROCESSING:
            if mission.current_step:
                detail = mission.current_step[:72]
            elif activity.summary:
                detail = activity.summary[:72]
            else:
                detail = "Keeping continuity, learning context, and execution posture aligned."
            return f"Processing with {route_label}. {detail}", "#2d1d08", "#ffe2b6"
        if snapshot.state is DesktopWidgetState.ERROR:
            return "Recovery mode. Jarvis is ready for a clean retry.", "#33181b", "#fecaca"
        if snapshot.online and mission.current_topic:
            return f"Ready with {route_label}. Active mission: {mission.current_topic}", "#123624", "#bff8f0"
        if snapshot.online:
            return f"Ready with {route_label}. Enter sends, Shift+Enter adds a newline.", "#101b31", "#d7e6ff"
        return "Desktop services are offline. The launcher stays available while Jarvis reconnects.", "#1f2937", "#d1d5db"

    def _render_continuity_snapshot(self, snapshot: Any) -> None:
        """Render the conversation continuity rail from controller-owned truth."""
        continuity = snapshot.continuity
        counts_var = getattr(self, "_continuity_counts_var", None)
        summary_var = getattr(self, "_continuity_summary_var", None)
        anchor_var = getattr(self, "_continuity_anchor_var", None)
        goal_var = getattr(self, "_continuity_goal_var", None)
        decision_var = getattr(self, "_continuity_decision_var", None)
        if counts_var is not None:
            counts_var.set(
                f"{len(continuity.anchor_entities)} anchors · "
                f"{len(continuity.unresolved_goals)} goals · "
                f"{len(continuity.prior_decisions)} decisions · "
                f"{continuity.timeline_count} turns"
            )
        if summary_var is not None:
            summary_var.set(
                continuity.rolling_summary
                or "Jarvis will surface key entities, unresolved goals, and prior decisions here."
            )
        if anchor_var is not None:
            anchor_var.set(self._snapshot_continuity_items(continuity.anchor_entities, "No anchors yet"))
        if goal_var is not None:
            goal_var.set(self._snapshot_continuity_items(continuity.unresolved_goals, "No unresolved goals yet"))
        if decision_var is not None:
            decision_var.set(self._snapshot_continuity_items(continuity.prior_decisions, "No prior decisions yet"))

    @staticmethod
    def _snapshot_continuity_items(items: tuple[str, ...] | list[str], empty_text: str) -> str:
        """Return a compact continuity list string with overflow summarization."""
        materialized = [str(item).strip() for item in items if str(item).strip()]
        if not materialized:
            return empty_text
        visible = materialized[:3]
        remainder = len(materialized) - len(visible)
        suffix = f" +{remainder} more" if remainder > 0 else ""
        return " · ".join(visible) + suffix

    def _snapshot_health_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return the health pulse chip content and palette."""
        diagnostics = snapshot.diagnostics
        if diagnostics.score is None:
            return "Health --", "#1f2937", "#d1d5db"
        if diagnostics.healthy and diagnostics.issue_count == 0:
            return f"Health {diagnostics.score}", "#123624", "#b7f0cf"
        if diagnostics.score >= 70:
            return f"Health {diagnostics.score} · {diagnostics.issue_count}", "#3a2a12", "#fde68a"
        return f"Health {diagnostics.score} · {diagnostics.issue_count}", "#3b1212", "#fecaca"

    def _render_diagnostics_snapshot(self, snapshot: Any) -> None:
        """Render the diagnostics drawer content from controller-owned truth."""
        diagnostics = snapshot.diagnostics
        summary_var = getattr(self, "_diagnostics_summary_var", None)
        detail_var = getattr(self, "_diagnostics_detail_var", None)
        issue_var = getattr(self, "_diagnostics_issue_var", None)
        summary_label = getattr(self, "_diagnostics_summary_label", None)
        issue_label = getattr(self, "_diagnostics_issue_label", None)

        if diagnostics.score is None:
            summary_text = "Health status will appear here after the first quick scan."
            detail_text = "Quick diagnostics have not run yet. Use the health chip or Diagnose button to refresh."
            issue_text = "No quick-scan issues recorded yet."
            summary_color = self.MUTED
            issue_color = "#d7e6ff"
        elif diagnostics.healthy and diagnostics.issue_count == 0:
            summary_text = f"Quick health score {diagnostics.score}. No active issues detected."
            detail_text = "Database, memory pressure, and gateway quick checks are within the healthy range."
            issue_text = "No quick-scan issues recorded."
            summary_color = self.ACCENT
            issue_color = "#bff8f0"
        else:
            summary_text = f"Quick health score {diagnostics.score}. {diagnostics.issue_count} issue(s) need attention."
            detail_text = diagnostics.top_issue or "Diagnostics reported issues but did not provide detail."
            issue_text = "Open Diagnose & Repair for a deeper scan and auto-fix options."
            summary_color = "#fde68a" if diagnostics.score >= 70 else "#fecaca"
            issue_color = "#fde68a" if diagnostics.score >= 70 else "#fecaca"

        if summary_var is not None:
            summary_var.set(summary_text)
        if detail_var is not None:
            detail_var.set(detail_text)
        if issue_var is not None:
            issue_var.set(issue_text)
        if summary_label is not None:
            summary_label.config(fg=summary_color)
        if issue_label is not None:
            issue_label.config(fg=issue_color)

    def _snapshot_intel_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return the compact intelligence pill in the live capsule."""
        if snapshot.intelligence_score_pct is None:
            return "Intel --", "#1f2937", "#d1d5db"
        if snapshot.intelligence_regression:
            return f"Intel {snapshot.intelligence_score_pct}% LOW", "#3b1212", "#fecaca"
        if snapshot.intelligence_score_pct >= 70:
            return f"Intel {snapshot.intelligence_score_pct}%", "#123624", "#b7f0cf"
        if snapshot.intelligence_score_pct >= 50:
            return f"Intel {snapshot.intelligence_score_pct}%", "#3a2a12", "#fde68a"
        return f"Intel {snapshot.intelligence_score_pct}%", "#3b1212", "#fecaca"

    def _render_session_snapshot(self, snapshot: Any) -> None:
        """Render route/security/voice posture chips from the controller snapshot."""
        session = snapshot.session
        route_chip_var = getattr(self, "_route_chip_var", None)
        route_chip_label = getattr(self, "_route_chip_label", None)
        route_text, route_bg, route_fg = self._snapshot_route_chip(snapshot)
        if route_chip_var is not None:
            route_chip_var.set(route_text)
        if route_chip_label is not None:
            route_chip_label.config(
                bg=route_bg,
                fg=route_fg,
                highlightbackground=session.route_accent,
                highlightcolor=session.route_accent,
                highlightthickness=1,
            )

        control_chip_var = getattr(self, "_control_chip_var", None)
        control_chip_label = getattr(self, "_control_chip_label", None)
        control_text, control_bg, control_fg = self._snapshot_control_chip(snapshot)
        if control_chip_var is not None:
            control_chip_var.set(control_text)
        if control_chip_label is not None:
            control_chip_label.config(bg=control_bg, fg=control_fg)

        approval_chip_var = getattr(self, "_approval_chip_var", None)
        approval_chip_label = getattr(self, "_approval_chip_label", None)
        approval_text, approval_bg, approval_fg = self._snapshot_approval_chip(snapshot)
        if approval_chip_var is not None:
            approval_chip_var.set(approval_text)
        if approval_chip_label is not None:
            approval_chip_label.config(bg=approval_bg, fg=approval_fg)

        voice_chip_var = getattr(self, "_voice_chip_var", None)
        voice_chip_label = getattr(self, "_voice_chip_label", None)
        voice_text, voice_bg, voice_fg = self._snapshot_voice_chip(snapshot)
        if voice_chip_var is not None:
            voice_chip_var.set(voice_text)
        if voice_chip_label is not None:
            voice_chip_label.config(bg=voice_bg, fg=voice_fg)

        if session.speech_enabled and voice_chip_label is not None:
            voice_chip_label.config(highlightthickness=0)

    def _snapshot_route_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return route-chip text and palette for the current model posture."""
        session = snapshot.session
        label = session.route_label[:22] or "Auto Router"
        family = session.route_family
        if family == "cli":
            return label, "#1f3b2d", "#c9f7e3"
        if family == "cloud":
            return label, "#3a2610", "#fde7c3"
        return label, "#123624", "#b7f0cf"

    def _snapshot_control_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return desktop-action posture chip content."""
        session = snapshot.session
        if session.control_armed:
            return session.control_mode, "#163857", "#d7ebff"
        return session.control_mode, "#1b2940", "#d7e6ff"

    def _snapshot_approval_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return approval-mode chip content."""
        session = snapshot.session
        if session.auto_approve:
            return session.approval_mode, "#3b1212", "#fecaca"
        return session.approval_mode, "#3a2a12", "#fde68a"

    def _snapshot_voice_chip(self, snapshot: Any) -> tuple[str, str, str]:
        """Return voice-mode chip content."""
        session = snapshot.session
        text = session.voice_mode
        if not session.speech_enabled:
            text = f"{text} · Silent"
        if session.wakeword_enabled:
            return text, "#0f2b5b", "#bfdbfe"
        return text, "#172554", "#bfdbfe"

    def _render_mission_progress(self, snapshot: Any) -> None:
        """Render the live mission progress track inside the hero capsule."""
        progress_var = getattr(self, "_mission_progress_var", None)
        pct_var = getattr(self, "_mission_progress_pct_var", None)
        progress_label = getattr(self, "_mission_progress_label", None)
        pct_label = getattr(self, "_mission_progress_pct_label", None)
        progress_canvas = getattr(self, "_mission_progress_canvas", None)
        progress_fill = getattr(self, "_mission_progress_fill", None)
        progress_glow = getattr(self, "_mission_progress_glow", None)
        if progress_canvas is None or progress_fill is None or progress_glow is None:
            return

        mission = snapshot.mission
        progress_pct = max(0, min(100, int(mission.progress_pct or 0)))
        if mission.current_topic:
            detail = f"{mission.artifacts_so_far} artifacts" if mission.artifacts_so_far else "live mission"
            text = f"{mission.current_topic[:28]} · {detail}"
        elif mission.count > 0:
            text = f"{mission.count} live mission{'s' if mission.count != 1 else ''} staged"
        elif snapshot.online:
            text = "Continuity primed"
        else:
            text = "Waiting for desktop brain"
        if progress_var is not None:
            progress_var.set(text)
        if pct_var is not None:
            pct_var.set(f"{progress_pct}%")

        fill_color, glow_color, label_color = self._mission_progress_palette(snapshot)
        if progress_label is not None:
            progress_label.config(fg=label_color)
        if pct_label is not None:
            pct_label.config(fg=label_color)
        width = max(int(progress_canvas.winfo_width() or 390), 120)
        fill_width = 2 + int((width - 4) * (progress_pct / 100.0))
        fill_width = max(fill_width, 2 if progress_pct <= 0 else 6)
        try:
            progress_canvas.itemconfig(self._mission_progress_track, fill="#13243d", outline="#214162")
            progress_canvas.itemconfig(progress_fill, fill=fill_color)
            progress_canvas.itemconfig(progress_glow, fill=glow_color)
            progress_canvas.coords(self._mission_progress_track, 1, 3, width - 1, 13)
            progress_canvas.coords(progress_fill, 2, 4, fill_width, 12)
            glow_start = max(2, fill_width - 42)
            progress_canvas.coords(progress_glow, glow_start, 4, fill_width, 12)
        except (tk.TclError, RuntimeError):
            logger.debug("Mission progress render skipped (widget may be destroyed)")

    def _mission_progress_palette(self, snapshot: Any) -> tuple[str, str, str]:
        """Return fill/glow/label colors for the mission progress track."""
        if snapshot.state is DesktopWidgetState.PROCESSING:
            return "#ffb347", "#ffe2a6", "#ffe2b6"
        if snapshot.state is DesktopWidgetState.LISTENING:
            return "#4da9ff", "#93c5fd", "#cae1ff"
        if snapshot.state is DesktopWidgetState.ERROR:
            return "#f87171", "#fecaca", "#fecaca"
        if snapshot.mission.current_topic:
            return "#34d3ba", "#9cefe1", "#bff8f0"
        if snapshot.online:
            return "#34d3ba", "#8ee6d7", "#8fb4d8"
        return "#6b7280", "#9ca3af", "#94a3b8"

    def _render_growth_snapshot(self, snapshot: Any) -> None:
        """Render Brain Growth metrics from the controller-owned snapshot."""
        growth_labels = getattr(self, "_growth_labels", None)
        if not growth_labels:
            return
        growth_labels["facts"].config(
            text=f"{snapshot.facts_total} (+{snapshot.facts_last_7d} 7d)" if snapshot.online else "--",
            fg=self.TEXT if snapshot.online else self.MUTED,
        )
        growth_labels["kg"].config(
            text=f"{snapshot.kg_nodes} nodes / {snapshot.kg_edges} edges" if snapshot.online else "--",
            fg=self.TEXT if snapshot.online else self.MUTED,
        )
        growth_labels["memory"].config(
            text=f"{snapshot.memory_records} records" if snapshot.online else "--",
            fg=self.TEXT if snapshot.online else self.MUTED,
        )
        mission = snapshot.mission
        if mission.current_topic:
            detail = mission.current_step or "Running"
            growth_labels["missions"].config(
                text=f"{mission.current_topic}: {detail[:36]}",
                fg=self.ACCENT_2,
            )
        elif mission.count > 0 and mission.topics:
            growth_labels["missions"].config(
                text=f"{mission.count} active: {', '.join(mission.topics)}",
                fg=self.ACCENT,
            )
        elif mission.count > 0:
            growth_labels["missions"].config(text=f"{mission.count} active", fg=self.ACCENT)
        else:
            growth_labels["missions"].config(text="None active", fg=self.MUTED)
        score_pct = snapshot.self_test_score_pct
        score_color = self.MUTED
        score_text = "--"
        if score_pct is not None:
            score_text = f"{score_pct}%"
            score_color = self.ACCENT if score_pct >= 70 else "#eab308" if score_pct >= 50 else self.WARN
        growth_labels["score"].config(text=score_text, fg=score_color)
        trend = snapshot.growth_trend
        trend_symbol = "\u25B2" if trend == "increasing" else "\u25BC" if trend == "declining" else "\u25C6"
        trend_color = "#22c55e" if trend == "increasing" else self.WARN if trend == "declining" else "#eab308"
        growth_labels["trend"].config(text=f"{trend_symbol} {trend}", fg=trend_color)

    def _live_capsule_palette(self) -> tuple[str, str]:
        """Return animated base/accent colors for the live capsule."""
        snapshot = self._ensure_controller().snapshot()
        if snapshot.state is DesktopWidgetState.LISTENING:
            return "#0b1d33", "#4da9ff"
        if snapshot.state is DesktopWidgetState.PROCESSING:
            return "#261707", "#ffb347"
        if snapshot.state is DesktopWidgetState.ERROR:
            return "#2a1014", "#ff7b7b"
        if snapshot.online:
            return "#0a1828", "#34d3ba"
        return "#12172a", "#8b9dff"

    def _live_signal_palette(self, snapshot: Any) -> tuple[str, str]:
        """Return base/fill colors for the animated signal bars."""
        if snapshot.state is DesktopWidgetState.LISTENING:
            return "#16324f", "#4da9ff"
        if snapshot.state is DesktopWidgetState.PROCESSING:
            return "#3b250e", "#ffb347"
        if snapshot.state is DesktopWidgetState.ERROR:
            return "#34151a", "#f87171"
        if snapshot.online:
            return "#163328", "#34d3ba"
        return "#20263a", "#8b9dff"

    def _animate_live_signal_bars(self, snapshot: Any, pulse: float) -> None:
        """Animate the compact signal meter inside the live capsule."""
        signal_canvas = getattr(self, "_live_signal_canvas", None)
        bars = getattr(self, "_live_signal_bars", None)
        if signal_canvas is None or not isinstance(bars, list):
            return
        base_color, fill_color = self._live_signal_palette(snapshot)
        amplitude = 0.18
        speed = 1.0
        if snapshot.state is DesktopWidgetState.LISTENING:
            amplitude = 0.72
            speed = 1.9
        elif snapshot.state is DesktopWidgetState.PROCESSING:
            amplitude = 0.92
            speed = 2.5
        elif snapshot.state is DesktopWidgetState.ERROR:
            amplitude = 0.42
            speed = 0.8
        elif snapshot.online:
            amplitude = 0.4
            speed = 1.2
        for index, bar_id in enumerate(bars):
            phase = (time.monotonic() - self._anim_t0) * speed + index * 0.6
            bar_height = 4 + int((14 * amplitude) * (0.35 + 0.65 * (0.5 + 0.5 * math.sin(phase))))
            x1 = 4 + index * 10
            x2 = x1 + 6
            y2 = 18
            y1 = y2 - bar_height
            signal_canvas.itemconfig(bar_id, fill=fill_color, outline="")
            signal_canvas.coords(bar_id, x1, y1, x2, y2)
        signal_canvas.config(bg=self._live_capsule.cget("bg"))
        if pulse > 0.7:
            signal_canvas.configure(highlightthickness=1, highlightbackground=base_color)
        else:
            signal_canvas.configure(highlightthickness=0)

    def _animate_live_capsule(self) -> None:
        """Animate the live capsule border so desktop status feels alive."""
        if self.stop_event.is_set():
            return
        capsule = getattr(self, "_live_capsule", None)
        if capsule is None:
            self._live_capsule_after_id = self.after(120, self._animate_live_capsule)
            return
        interval = self._live_capsule_animation_interval()
        if not self._panel_visible():
            self._live_capsule_after_id = self.after(interval, self._animate_live_capsule)
            return
        try:
            snapshot = self._ensure_controller().snapshot()
            base_color, accent_color = self._live_capsule_palette()
            pulse = 0.5 + 0.5 * math.sin((time.monotonic() - self._anim_t0) * 2.2)
            thickness = 1 if pulse < 0.55 else 2
            capsule.config(
                bg=base_color,
                highlightbackground=accent_color,
                highlightcolor=accent_color,
                highlightthickness=thickness,
            )
            for widget_name in (
                "_live_capsule_top",
                "_live_capsule_bottom",
                "_live_detail_body",
                "_live_ops_rail",
                "_live_mode_label",
                "_live_context_label",
                "_mission_progress_label",
                "_mission_progress_pct_label",
            ):
                widget = getattr(self, widget_name, None)
                if widget is not None:
                    widget.config(bg=base_color)
            progress_canvas = getattr(self, "_mission_progress_canvas", None)
            if progress_canvas is not None:
                progress_canvas.config(bg=base_color)
            self._animate_live_signal_bars(snapshot, pulse)
            self._live_capsule_after_id = self.after(interval, self._animate_live_capsule)
        except (tk.TclError, RuntimeError):
            logger.debug("Live capsule animation stopped (widget may be destroyed)")

    def _live_capsule_animation_interval(self) -> int:
        """Return the hero-surface animation cadence for the current visibility/state."""
        if not self._panel_visible():
            return 280
        snapshot = self._ensure_controller().snapshot()
        if snapshot.state in {DesktopWidgetState.LISTENING, DesktopWidgetState.PROCESSING}:
            return 75
        return 110

    def _handle_transport_failure(
        self,
        action: str,
        exc: Exception,
        *,
        hints: tuple[tuple[str, str], ...] = (),
        notify_toast: tuple[str, str, str] | None = None,
        command_mode: bool = False,
    ) -> None:
        """Map common transport failures to consistent widget output."""
        if isinstance(exc, HTTPError):
            message = f"{action} failed: {_http_error_details(exc)}"
        elif isinstance(exc, URLError):
            message = "Cannot reach Jarvis server." if action == "Connect" else "Cannot connect to Jarvis services."
        elif isinstance(exc, (RuntimeError, TimeoutError)):
            message = f"{action} failed: {exc}"
        else:
            logger.debug("%s failed: %s", action, exc)
            message = f"{action} failed: {exc}"

        if command_mode:
            self._handle_command_error(message)
        else:
            self._log_async(message, role="error")

        for hint, role in hints:
            self._log_async(hint, role=role)
        if notify_toast is not None:
            title, body, level = notify_toast
            self._notify_toast(title, body, level)

    def _on_tab_cycle_model(self, event: tk.Event[Any] | None = None) -> str:
        """Tab key in command text: cycle through model rotation."""
        self._model_index = (self._model_index + 1) % len(self.MODEL_ROTATION)
        self._update_model_label()
        self._push_session_snapshot()
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

    def _selected_model_snapshot(self) -> tuple[str, str, str]:
        """Return label/accent/family metadata for the selected model route."""
        alias, display_name, best_use, accent = self.MODEL_ROTATION[self._model_index]
        if alias == "auto":
            return "Auto Router", accent, "auto"
        if alias.endswith("-cli") or alias == "planner-cli":
            return display_name, accent, "cli"
        return display_name, accent, "cloud"

    def _push_session_snapshot(self, *, render: bool = True) -> None:
        """Push local desktop posture into the controller-owned snapshot."""
        controller = getattr(self, "_controller", None)
        if controller is None:
            controller = JarvisDesktopWidget._ensure_controller(self)
        route_label, route_accent, route_family = JarvisDesktopWidget._selected_model_snapshot(self)
        execute_var = getattr(self, "execute_var", None)
        priv_var = getattr(self, "priv_var", None)
        hotword_var = getattr(self, "hotword_var", None)
        speak_var = getattr(self, "speak_var", None)
        controller.apply_session_snapshot(
            route_label=route_label,
            route_accent=route_accent,
            route_family=route_family,
            control_armed=bool(execute_var.get()) if execute_var is not None else False,
            auto_approve=bool(priv_var.get()) if priv_var is not None else False,
            wakeword_enabled=bool(hotword_var.get()) if hotword_var is not None else False,
            speech_enabled=bool(speak_var.get()) if speak_var is not None else True,
        )
        if render:
            self._render_live_snapshot()

    def _on_posture_toggle(self) -> None:
        """Refresh the controller snapshot after desktop posture toggles change."""
        self._push_session_snapshot()

    def _on_hotword_toggle(self) -> None:
        """Update hotword execution and refresh desktop posture surfaces."""
        self._hotword_changed()
        self._push_session_snapshot()

    def _on_escape(self) -> None:
        """ESC key handler: cancel command if processing, otherwise minimize."""
        if self._widget_state == "processing":
            self._cancel_command()
        else:
            self._toggle_min()

    def _cancel_command(self) -> None:
        """Cancel the current in-progress command, stop TTS, and reset state."""
        JarvisDesktopWidget._ensure_controller(self).cancel_command()
        self._cancel_processing_timeout()
        self._hide_thinking()
        # Kill any running TTS processes
        try:
            import subprocess
            # Kill edge-tts and PowerShell speech processes
            for proc_name in ["edge-tts", "edge-playback"]:
                subprocess.run(
                    [_taskkill_executable(), "/F", "/IM", f"{proc_name}.exe"],
                    capture_output=True, timeout=5,
                )
            # Kill PowerShell speech synthesis if running
            subprocess.run(
                [_powershell_executable(), "-Command",
                 "Get-Process | Where-Object {$_.MainWindowTitle -eq '' -and $_.ProcessName -eq 'powershell'} | Stop-Process -Force -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.debug("Failed to kill TTS/speech processes during cancel: %s", exc)
        self.command_text.config(state=tk.NORMAL)
        self._log("Command cancelled.", role="system")

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
        if JarvisDesktopWidget._ensure_controller(self).processing_timed_out():
            self._hide_thinking()
            timeout_s = max(1, self._processing_timeout_ms // _MS_PER_SECOND)
            self._log(f"Command timed out ({timeout_s}s). Ready for new commands.", role="error")
            self.command_text.config(state=tk.NORMAL)

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
        if not JarvisDesktopWidget._ensure_controller(self).can_begin_command():
            self._log("Still processing previous command. Please wait...", role="system")
            return None
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
            controller = JarvisDesktopWidget._ensure_controller(self)
            if not controller.owns_generation(gen):
                return  # Stale worker -- a newer command owns the state
            self._cancel_processing_timeout()
            self._hide_thinking()
            try:
                self.command_text.config(state=tk.NORMAL)
            except tk.TclError:
                logger.debug("Failed to re-enable command input after processing")
            controller.complete_command(gen)
        return _cleanup

    def _send_command_async(self) -> None:
        text = self._validate_and_prepare_command()
        if text is None:
            return
        controller = JarvisDesktopWidget._ensure_controller(self)
        # Clear command text immediately after reading
        self.command_text.delete("1.0", tk.END)
        self._log(text, role="user")
        gen = controller.begin_command()
        if gen is None:
            self._log("Still processing previous command. Please wait...", role="system")
            return
        self._cmd_generation = controller.command_generation
        self._show_thinking()
        self.command_text.config(state=tk.DISABLED)
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
            except _REMOTE_WIDGET_ERRORS as exc:
                self._handle_transport_failure(
                    "Command",
                    exc,
                    hints=(("Make sure the Assistant and Mobile API are running.", "error"),),
                    command_mode=True,
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as exc:
                self._handle_transport_failure(
                    "Command",
                    exc,
                    hints=(("Make sure the Assistant and Mobile API are running.", "error"),),
                    command_mode=True,
                )
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
            except _WIDGET_IO_ERRORS as exc:
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
            from jarvis_engine.ops.process_manager import list_services
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
                    elif s < _SECONDS_PER_HOUR:
                        uptime_lbl.config(text=f"{s // 60}m")
                    else:
                        uptime_lbl.config(text=f"{s // _SECONDS_PER_HOUR}h {(s % _SECONDS_PER_HOUR) // 60}m")
                else:
                    dot.config(text="\u25CB", fg=self.MUTED)
                    uptime_lbl.config(text="stopped")
                    # Notify if service was previously running (crash detected)
                    if self._prev_svc_running.get(name, False):
                        self._notify_toast("Jarvis Service Down", f"{name} has stopped", "Warning")
                self._prev_svc_running[name] = svc["running"]
        except (ImportError, OSError, KeyError, TypeError, ValueError) as exc:
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
            except _REMOTE_WIDGET_ERRORS as exc:
                self._handle_transport_failure(
                    "Dashboard",
                    exc,
                    hints=(("Make sure the Assistant and Mobile API are running.", "error"),),
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as exc:
                self._handle_transport_failure(
                    "Dashboard",
                    exc,
                    hints=(("Make sure the Assistant and Mobile API are running.", "error"),),
                )

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
            except _REMOTE_WIDGET_ERRORS as exc:
                self._handle_transport_failure(
                    "Activity load",
                    exc,
                    hints=(("Make sure the Assistant and Mobile API are running.", "error"),),
                )
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as exc:
                self._handle_transport_failure(
                    "Activity load",
                    exc,
                    hints=(("Make sure the Assistant and Mobile API are running.", "error"),),
                )

        self._thread(worker)

    def _dictate_async(self) -> None:
        # Guard: skip if already listening or processing to prevent overlapping voice
        controller = JarvisDesktopWidget._ensure_controller(self)
        if not controller.begin_dictation():
            return
        auto_send = bool(self.auto_send_var.get())

        def worker() -> None:
            try:
                text = _voice_dictate_once(timeout_s=8)
                if not text:
                    self._log_async("No speech recognized.", role="system")
                    self._set_state_async(DesktopWidgetState.IDLE.value)
                    return
                self._set_command_text_async(text)
                self._log_async(f"dictated: {text}", role="system")
                if auto_send:
                    # Reset to idle first so _send_command_async doesn't
                    # reject the command with "Still processing"
                    self._set_state_async(DesktopWidgetState.IDLE.value)
                    self.after(0, self._send_command_async)
                else:
                    self._set_state_async(DesktopWidgetState.IDLE.value)
            except _WIDGET_VOICE_ERRORS as exc:
                logger.debug("Voice dictation failed: %s", exc)
                self._log_async(f"dictation failed: {exc}", role="error")
                self._set_error_briefly_async()

        self._thread(worker)

    def _hotword_changed(self) -> None:
        if self.hotword_var.get():
            if not JarvisDesktopWidget._ensure_controller(self).try_start_hotword_loop():
                self._log("Wake Word loop already running.")
                return
            self._log("Wake Word enabled. Say 'Jarvis' to trigger dictation.")
            self._thread(self._hotword_loop)
        else:
            self._log("Wake Word disabled.")

    def _hotword_loop(self) -> None:
        try:
            self._hotword_loop_inner()
        finally:
            JarvisDesktopWidget._ensure_controller(self).finish_hotword_loop()

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
            except _WIDGET_VOICE_ERRORS as exc:
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
                    safe_url = _validated_widget_request_url(url)
                    req = Request(url=safe_url, method="GET")
                    resp = _safe_urlopen(req, timeout=5, context=ssl_ctx)
                    ok = resp.status == 200
                    if ok:
                        try:
                            body = resp.read().decode("utf-8")
                            health_payload = json.loads(body)
                            if isinstance(health_payload, dict) and "intelligence" in health_payload:
                                intel_data = health_payload["intelligence"]
                        except (json.JSONDecodeError, KeyError, ValueError, UnicodeDecodeError) as exc:
                            logger.debug("Failed to parse intelligence from health response: %s", exc)
                    resp.close()
                    resp = None
                    if ok:
                        break
                except _WIDGET_IO_ERRORS as exc:
                    logger.debug("Health poll request failed: %s", exc)
                    ok = False
                finally:
                    if resp is not None:
                        try:
                            resp.close()
                        except (OSError, RuntimeError) as exc:
                            logger.debug("Failed to close health poll HTTP response: %s", exc)
                        resp = None
                if self.stop_event.is_set():
                    break
                time.sleep(0.2)
            if ok or self.stop_event.is_set():
                break
        return ok, intel_data

    def _fetch_widget_status(
        self,
        cfg: WidgetConfig,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
        """Fetch growth, recent events, and current mission focus via /widget-status."""
        growth_data: dict[str, Any] | None = None
        recent_events: list[dict[str, Any]] = []
        now_working_on: dict[str, Any] | None = None
        try:
            ws = _http_json(cfg, "/widget-status", method="GET")
            growth_data = ws.get("growth") if isinstance(ws, dict) else None
            alerts = ws.get("alerts", []) if isinstance(ws, dict) else []
            recent_events = ws.get("recent_events", []) if isinstance(ws, dict) else []
            now_working_on = ws.get("now_working_on") if isinstance(ws, dict) else None
            if isinstance(alerts, list):
                for alert in alerts:
                    msg = str(alert.get("message", "")) if isinstance(alert, dict) else str(alert)
                    if msg:
                        self._notify_toast("Jarvis Alert", msg, "Warning")
                        break  # One toast per poll cycle
        except _WIDGET_IO_ERRORS as exc:
            logger.debug("Failed to fetch widget-status: %s", exc)
        return growth_data, recent_events, now_working_on

    def _should_poll_diagnostics(self) -> bool:
        """Return whether the quick diagnostics endpoint should be refreshed."""
        now = time.monotonic()
        last = getattr(self, "_last_diag_poll_at", 0.0)
        return (now - last) >= 45.0

    def _fetch_diagnostics_status(self, cfg: WidgetConfig) -> dict[str, Any] | None:
        """Fetch quick diagnostics status for the desktop health pulse."""
        try:
            data = _http_json(cfg, "/diagnostics/status", method="GET")
        except _WIDGET_IO_ERRORS as exc:
            logger.debug("Failed to fetch diagnostics status: %s", exc)
            return None
        self._last_diag_poll_at = time.monotonic()
        return data if isinstance(data, dict) else None

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
            now_working_on: dict[str, Any] | None = None
            diagnostics_data: dict[str, Any] | None = None
            if ok and cfg.token and cfg.signing_key:
                growth_data, recent_events, now_working_on = self._fetch_widget_status(cfg)
                if self._should_poll_diagnostics():
                    diagnostics_data = self._fetch_diagnostics_status(cfg)

            if not self.stop_event.is_set():
                try:
                    self.after(
                        0,
                        self._set_online,
                        ok,
                        intel_data,
                        growth_data,
                        recent_events,
                        now_working_on,
                        diagnostics_data,
                    )
                except (tk.TclError, RuntimeError):  # Widget may be destroyed
                    logger.debug("Cannot schedule online state update (widget may be destroyed)")
                    return
            self._health_sleep()

    def _set_online(self, value: bool, intel_data: dict[str, Any] | None = None,
                    growth_data: dict[str, Any] | None = None,
                    recent_events: list[dict[str, Any]] | None = None,
                    now_working_on: dict[str, Any] | None = None,
                    diagnostics_data: dict[str, Any] | None = None) -> None:
        """Update online state and refresh status — always call on main thread."""
        self.online = value
        controller = self._ensure_controller()
        new_events = controller.apply_health_snapshot(
            online=value,
            intel_data=intel_data,
            growth_data=growth_data,
            recent_events=recent_events,
            now_working_on=now_working_on,
            clear_missing=True,
        )
        if diagnostics_data is not None:
            controller.apply_diagnostics_snapshot(
                score=diagnostics_data.get("score") if isinstance(diagnostics_data.get("score"), int) else None,
                healthy=bool(diagnostics_data.get("healthy", False)),
                issues=diagnostics_data.get("issues") if isinstance(diagnostics_data.get("issues"), list) else [],
                error=str(diagnostics_data.get("error", "")),
            )
        if new_events:
            self._mark_launcher_attention(new_events)
            self._log_activity_events(new_events)
        self._render_live_snapshot()

    def _update_growth_labels(self, growth_data: dict[str, Any] | None) -> None:
        """Update Brain Growth labels through the controller-owned snapshot."""
        controller = self._ensure_controller()
        controller.apply_health_snapshot(
            online=self.online,
            growth_data=growth_data,
        )
        self._render_live_snapshot()

    def _update_activity_events(self, events: list[dict[str, Any]]) -> None:
        """Update controller-owned activity digest and display any new events."""
        controller = getattr(self, "_controller", None)
        if not isinstance(controller, DesktopInteractionController):
            JarvisDesktopWidget._update_activity_events_legacy(self, events)
            return

        new_events = self._ensure_controller().apply_health_snapshot(
            online=self.online,
            recent_events=events,
        )
        if new_events:
            self._mark_launcher_attention(new_events)
            self._log_activity_events(new_events)
        self._render_live_snapshot()

    def _update_activity_events_legacy(self, events: list[dict[str, Any]]) -> None:
        """Legacy dedupe/log path used by lightweight unit-test stubs."""
        _CAT_ROLE = {
            "error": "error",
            "security": "error",
        }
        seen_event_ids = getattr(self, "_seen_event_ids", {})
        new_events = []
        for evt in events:
            eid = str(evt.get("event_id", ""))
            if eid and eid in seen_event_ids:
                continue
            if eid:
                seen_event_ids[eid] = None
            new_events.append(evt)
        if len(seen_event_ids) > 500:
            keys = list(seen_event_ids.keys())
            self._seen_event_ids = dict.fromkeys(keys[-400:])
        else:
            self._seen_event_ids = seen_event_ids
        if not new_events:
            return
        for evt in reversed(new_events):
            ts_raw = str(evt.get("timestamp", ""))
            ts_short = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
            cat = str(evt.get("category", ""))
            summary = str(evt.get("summary", ""))
            role = _CAT_ROLE.get(cat, "system")
            self._log(f"\u26a1 [{ts_short}] [{cat.upper()}] {summary}", role=role)

    def _log_activity_events(self, events: list[dict[str, Any]]) -> None:
        """Display deduped activity events in the conversation output."""
        _CAT_ROLE = {
            "error": "error",
            "security": "error",
        }
        if not events:
            return
        for evt in reversed(events):  # Oldest first
            ts_raw = str(evt.get("timestamp", ""))
            ts_short = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
            cat = str(evt.get("category", ""))
            summary = str(evt.get("summary", ""))
            role = _CAT_ROLE.get(cat, "system")
            self._log(f"\u26a1 [{ts_short}] [{cat.upper()}] {summary}", role=role)

    def _update_intelligence_label(self, intel_data: dict[str, Any] | None) -> None:
        """Update the intelligence score via the controller-owned snapshot."""
        self._ensure_controller().apply_health_snapshot(
            online=self.online,
            intel_data=intel_data,
        )
        self._render_live_snapshot()

    def _set_state(self, state: str) -> None:
        """Update the visual state machine: idle | listening | processing | error."""
        JarvisDesktopWidget._ensure_controller(self).set_state(state)

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
        self._refresh_continuity_snapshot()
        self._render_live_snapshot()

    def _refresh_continuity_snapshot(self) -> None:
        """Pull continuity state into the controller for desktop rendering."""
        try:
            from jarvis_engine.conversation_state import get_conversation_state

            csm = get_conversation_state()
            injection = csm.get_prompt_injection()
            state_snapshot = csm.get_state_snapshot(full=False)
        except (ImportError, OSError, RuntimeError, ValueError, KeyError, AttributeError) as exc:
            logger.debug("Failed to refresh continuity snapshot: %s", exc)
            return

        self._ensure_controller().apply_continuity_snapshot(
            rolling_summary=str(injection.get("rolling_summary", "")).strip(),
            anchor_entities=list(injection.get("anchor_entities", [])),
            unresolved_goals=list(injection.get("unresolved_goals", [])),
            prior_decisions=list(injection.get("prior_decisions", [])),
            timeline_count=int(state_snapshot.get("timeline_count", 0) or 0),
        )


def run_desktop_widget() -> None:
    app = JarvisDesktopWidget(_repo_root())
    app.mainloop()
