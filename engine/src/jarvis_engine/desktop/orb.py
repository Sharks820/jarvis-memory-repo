"""Orb animation logic for the Jarvis desktop widget.

Contains the ``OrbAnimationMixin`` class that provides all launcher orb
and panel status-orb animation methods.  Extracted from ``desktop_widget.py``
to improve separation of concerns.

Classes and methods in this module are intended to be mixed into
``JarvisDesktopWidget`` via multiple inheritance; they should NOT be
used standalone.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
import threading
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any, Protocol, cast

from jarvis_engine.desktop.helpers import (
    _is_position_on_screen,
    _save_widget_cfg,
    _snap_to_edge,
)

logger = logging.getLogger(__name__)

class _OrbHost(Protocol):
    stop_event: threading.Event
    _anim_t0: float
    orb_canvas: tk.Canvas
    orb_id: int
    _orb_sweep: int | None
    launcher_win: tk.Toplevel | None
    launcher_canvas: tk.Canvas | None
    _l_arc1: int | None
    _l_arc2: int | None
    _l_arc3: int | None
    _l_arc4: int | None
    _l_core: int | None
    _l_glow: int | None
    _l_badge_bg: int | None
    _l_badge_text: int | None
    _l_particles: list[int]
    _launcher_size: int
    _widget_state: str
    online: bool
    cfg: Any
    root_path: Path
    ACCENT: str
    ACCENT_2: str
    WARN: str
    LAUNCHER_TRANSPARENT: str
    _orb_after_id: str | None
    _launcher_after_id: str | None
    _drag_offset_x: int
    _drag_offset_y: int
    _launcher_dragged: bool

    def after(self, ms: int, func: Callable[..., object] | None = None, *args: object) -> str: ...
    def _confirm_exit(self) -> None: ...
    def _show_panel(self) -> None: ...
    def _orb_color(self) -> str: ...
    def _animate_orb(self) -> None: ...
    def _launcher_start_drag(self, event: tk.Event[Any]) -> None: ...
    def _launcher_drag(self, event: tk.Event[Any]) -> None: ...
    def _launcher_release(self, event: tk.Event[Any]) -> None: ...
    def _save_launcher_position(self) -> None: ...
    def _launcher_state_speed(self) -> float: ...
    def _launcher_state_colors(self) -> tuple[str, str, str, str, str, str]: ...
    def _update_launcher_geometry(self, t: float, speed: float) -> None: ...
    def _apply_launcher_colors(self, colors: tuple[str, str, str, str, str, str]) -> None: ...
    def _animate_launcher(self) -> None: ...


class OrbAnimationMixin:
    """Mixin providing orb / launcher animation and launcher window management.

    Expects the host class to supply the following attributes:
      - ``self.stop_event``, ``self._anim_t0``
      - ``self.orb_canvas``, ``self.orb_id``, ``self._orb_sweep``
      - ``self.launcher_win``, ``self.launcher_canvas``
      - ``self._l_arc1`` ... ``self._l_glow``, ``self._l_particles``
      - ``self._launcher_size``, ``self._widget_state``, ``self.online``
      - ``self.cfg``, ``self.root_path``
      - Colour constants (``BG``, ``ACCENT``, etc.)
      - ``self.after()`` (tkinter scheduling)
    """

    launcher_win: tk.Toplevel | None
    launcher_canvas: tk.Canvas | None
    _l_arc1: int | None
    _l_arc2: int | None
    _l_arc3: int | None
    _l_arc4: int | None
    _l_core: int | None
    _l_glow: int | None
    _l_badge_bg: int | None
    _l_badge_text: int | None
    _orb_after_id: str | None
    _launcher_after_id: str | None

    # ------------------------------------------------------------------
    # Panel status orb
    # ------------------------------------------------------------------

    def _orb_color(self: Any) -> str:
        """Return the current orb color based on widget state."""
        state = self._widget_state
        if state == "listening":
            return self.ACCENT_2  # blue
        if state == "processing":
            return "#ff9f43"  # orange
        if state == "error":
            return self.WARN  # red
        # idle: color depends on online status
        return self.ACCENT if self.online else "#6366f1"

    def _orb_animation_interval(self: Any) -> int:
        """Return the panel-orb animation cadence based on panel visibility."""
        panel_visible = getattr(self, "_panel_visible", lambda: True)()
        return 33 if panel_visible else 220

    def _animate_orb(self: Any) -> None:
        if self.stop_event.is_set():
            return
        interval = self._orb_animation_interval()
        if not getattr(self, "_panel_visible", lambda: True)():
            self._orb_after_id = self.after(interval, self._animate_orb)
            return
        t = time.monotonic() - self._anim_t0
        color = self._orb_color()
        # Smooth breathing pulse (time-based, ~30fps)
        breath = math.sin(t * 3.0)
        pulse = 6.0 + breath * 1.5
        cx, cy = 15.0, 15.0
        # Scanning sweep rotation
        sweep_angle = (t * 120) % 360
        try:
            self.orb_canvas.coords(self.orb_id, cx - pulse, cy - pulse, cx + pulse, cy + pulse)
            self.orb_canvas.itemconfig(self.orb_id, fill=color)
            if self._orb_sweep is not None:
                self.orb_canvas.itemconfig(self._orb_sweep, start=sweep_angle, outline=color)
            self._orb_after_id = self.after(interval, self._animate_orb)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Orb animation stopped (widget may be destroyed)")
            return

    # ------------------------------------------------------------------
    # Launcher window (build, drag, position)
    # ------------------------------------------------------------------

    def _build_launcher(self: Any) -> None:
        launcher = tk.Toplevel(cast(tk.Misc, self))
        launcher.overrideredirect(True)
        launcher.attributes("-topmost", True)
        launcher.configure(bg=self.LAUNCHER_TRANSPARENT)
        try:
            launcher.wm_attributes("-transparentcolor", self.LAUNCHER_TRANSPARENT)
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to set launcher transparent color attribute: %s", exc)
        size = self._launcher_size
        # Restore saved launcher position or default to bottom-right
        lx, ly = self.cfg.launcher_x, self.cfg.launcher_y
        if lx is not None and ly is not None and _is_position_on_screen(lx, ly, launcher):
            x, y = lx, ly
        else:
            screen_w = launcher.winfo_screenwidth()
            screen_h = launcher.winfo_screenheight()
            x = max(8, screen_w - size - 24)
            y = max(8, screen_h - size - 96)
        launcher.geometry(f"{size}x{size}+{x}+{y}")

        canvas = tk.Canvas(
            launcher,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            bg=self.LAUNCHER_TRANSPARENT,
            cursor="hand2",
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        cx, cy = size / 2, size / 2
        # Outer glow halo (breathing) — thicker for visibility
        self._l_glow = canvas.create_oval(2, 2, size - 2, size - 2, outline="#0d3d36", width=3)
        # Arc 4: outermost decorative ring, thin, slow counter-rotate
        self._l_arc4 = canvas.create_arc(
            1, 1, size - 1, size - 1, start=0, extent=60,
            style=tk.ARC, outline="#2dd4bf", width=1,
        )
        # Rotating arc 1: outer ring, 240deg extent — thicker
        self._l_arc1 = canvas.create_arc(
            6, 6, size - 6, size - 6, start=0, extent=240,
            style=tk.ARC, outline="#2dd4bf", width=3,
        )
        # Rotating arc 2: mid ring, 160deg extent (counter-rotating)
        self._l_arc2 = canvas.create_arc(
            14, 14, size - 14, size - 14, start=120, extent=160,
            style=tk.ARC, outline="#0ea5e9", width=2,
        )
        # Rotating arc 3: inner fast ring, 90deg (processing indicator, hidden by default)
        self._l_arc3 = canvas.create_arc(
            21, 21, size - 21, size - 21, start=0, extent=90,
            style=tk.ARC, outline="#f59e0b", width=2, state=tk.HIDDEN,
        )
        # Core circle (breathing) with outline ring
        core_pad = 24
        self._l_core = canvas.create_oval(
            core_pad, core_pad, size - core_pad, size - core_pad,
            fill="#0f766e", outline="#2dd4bf", width=1,
        )
        # Orbiting particles (5 dots at different orbit radii for richer effect)
        self._l_particles = []
        for _ in range(5):
            pid = canvas.create_oval(0, 0, 5, 5, fill="#5eead4", outline="")
            self._l_particles.append(pid)
        # Center letter — larger, bolder
        canvas.create_text(cx, cy, text="J", fill="#ecfeff", font=("Segoe UI", 20, "bold"))
        self._l_badge_bg = canvas.create_oval(
            size - 30,
            size - 30,
            size - 8,
            size - 8,
            fill="#115e59",
            outline="#ccfbf1",
            width=1,
            state=tk.HIDDEN,
        )
        self._l_badge_text = canvas.create_text(
            size - 19,
            size - 19,
            text="",
            fill="#ccfbf1",
            font=("Segoe UI", 8, "bold"),
            state=tk.HIDDEN,
        )

        canvas.bind("<ButtonPress-1>", self._launcher_start_drag)
        canvas.bind("<B1-Motion>", self._launcher_drag)
        canvas.bind("<ButtonRelease-1>", self._launcher_release)
        canvas.bind("<Button-3>", lambda _e: self._confirm_exit())
        launcher.bind("<ButtonPress-1>", self._launcher_start_drag)
        launcher.bind("<B1-Motion>", self._launcher_drag)
        launcher.bind("<ButtonRelease-1>", self._launcher_release)
        launcher.bind("<Control-Shift-Q>", lambda _e: self._confirm_exit())
        self.launcher_win = launcher
        self.launcher_canvas = canvas

    def _launcher_start_drag(self: Any, event: tk.Event[Any]) -> None:
        self._drag_offset_x = int(event.x)
        self._drag_offset_y = int(event.y)
        self._launcher_dragged = False

    def _launcher_drag(self: Any, event: tk.Event[Any]) -> None:
        if self.launcher_win is None:
            return
        self._launcher_dragged = True
        x = int(self.launcher_win.winfo_x() + event.x - self._drag_offset_x)
        y = int(self.launcher_win.winfo_y() + event.y - self._drag_offset_y)
        self.launcher_win.geometry(f"+{x}+{y}")

    def _launcher_release(self: Any, _event: tk.Event[Any]) -> None:
        if self._launcher_dragged:
            self._save_launcher_position()
        else:
            self._show_panel()

    def _save_launcher_position(self: Any) -> None:
        """Save the current launcher orb position to config."""
        if self.launcher_win is None:
            return
        try:
            x = self.launcher_win.winfo_x()
            y = self.launcher_win.winfo_y()
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Cannot read launcher position (widget may be destroyed)")
            return
        x, y = _snap_to_edge(x, y, self._launcher_size, self._launcher_size, cast(tk.Misc, self))
        try:
            self.launcher_win.geometry(f"+{x}+{y}")
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Failed to apply snapped launcher geometry (widget may be destroyed)")
        self.cfg.launcher_x = x
        self.cfg.launcher_y = y
        try:
            _save_widget_cfg(self.root_path, cast(Any, self.cfg))
        except Exception as exc:  # boundary: catch-all justified
            logger.debug("Failed to save launcher position to config: %s", exc)

    # ------------------------------------------------------------------
    # Launcher animation (arcs, particles, glow, colors)
    # ------------------------------------------------------------------

    def _launcher_state_speed(self: Any) -> float:
        """Return animation speed multiplier based on widget state."""
        state = self._widget_state
        if state == "processing":
            return 2.5
        if state == "listening":
            return 1.6
        if state == "error":
            return 0.3
        return 1.0

    def _launcher_state_colors(self: Any) -> tuple[str, str, str, str, str, str]:
        """Return color palette (arc1, arc2, core, particles, glow, arc4) for current state."""
        state = self._widget_state
        if state == "listening":
            return ("#3b82f6", "#60a5fa", "#1e3a8a", "#93c5fd", "#1e3a5e", "#2563eb")
        if state == "processing":
            return ("#f59e0b", "#fbbf24", "#78350f", "#fde68a", "#3d2800", "#d97706")
        if state == "error":
            return ("#ef4444", "#f87171", "#7f1d1d", "#fca5a5", "#3d0d0d", "#dc2626")
        if self.online:
            return ("#2dd4bf", "#0ea5e9", "#0f766e", "#5eead4", "#0d3d36", "#14b8a6")
        return ("#6366f1", "#818cf8", "#312e81", "#a5b4fc", "#1e1b4b", "#7c3aed")

    def _launcher_animation_interval(self: Any) -> int:
        """Return the launcher animation cadence based on launcher visibility."""
        launcher_visible = getattr(self, "_launcher_visible", lambda: True)()
        return 33 if launcher_visible else 180

    def _launcher_badge_payload(self: Any, snapshot: Any) -> tuple[str, str, str, bool]:
        """Return launcher badge text/palette from controller-owned desktop truth."""
        state = getattr(getattr(snapshot, "state", ""), "value", getattr(snapshot, "state", ""))
        activity = getattr(snapshot, "activity", None)
        category = str(getattr(activity, "category", "") or "")
        mission = getattr(snapshot, "mission", None)
        mission_count = int(getattr(mission, "count", 0) or 0)
        if state == "error" or category in {"error", "security"}:
            return "!", "#7f1d1d", "#fecaca", True
        if state == "processing":
            return "AI", "#92400e", "#fde68a", True
        if state == "listening":
            return "V", "#1d4ed8", "#dbeafe", True
        if mission_count > 0:
            text = "9+" if mission_count > 9 else str(mission_count)
            return text, "#115e59", "#ccfbf1", True
        return "", "#115e59", "#ccfbf1", False

    def _update_launcher_badge(self: Any, snapshot: Any) -> None:
        """Render the launcher badge for live mission/activity state."""
        if self.launcher_canvas is None or self._l_badge_bg is None or self._l_badge_text is None:
            return
        text, bg, fg, visible = self._launcher_badge_payload(snapshot)
        state = tk.NORMAL if visible else tk.HIDDEN
        self.launcher_canvas.itemconfig(self._l_badge_bg, state=state, fill=bg, outline=fg)
        self.launcher_canvas.itemconfig(self._l_badge_text, state=state, text=text, fill=fg)

    def _update_launcher_geometry(self: Any, t: float, speed: float) -> None:
        """Update arc rotations, core breathing, particles, and glow positions."""
        assert self.launcher_canvas is not None
        size = self._launcher_size
        cx, cy = size / 2, size / 2
        state = self._widget_state

        # Arc 4: outermost decorative ring, slow counter-rotate with breathing extent
        if self._l_arc4 is not None:
            a4 = (360 - (t * 30 * speed) % 360) % 360
            ext4 = 50 + 20 * math.sin(t * 1.2 * speed)
            self.launcher_canvas.itemconfig(self._l_arc4, start=a4, extent=ext4)
        # Rotate arc 1: outer, clockwise with breathing extent
        if self._l_arc1 is not None:
            a1 = (t * 90 * speed) % 360
            ext1 = 220 + 40 * math.sin(t * 0.8 * speed)
            self.launcher_canvas.itemconfig(self._l_arc1, start=a1, extent=ext1)
        # Rotate arc 2: mid, counter-clockwise with breathing extent
        if self._l_arc2 is not None:
            a2 = (360 - (t * 60 * speed) % 360) % 360
            ext2 = 140 + 30 * math.sin(t * 1.4 * speed + 1.0)
            self.launcher_canvas.itemconfig(self._l_arc2, start=a2, extent=ext2)
        # Arc 3: fast inner ring, only visible during processing
        if self._l_arc3 is not None:
            if state == "processing":
                a3 = (t * 180 * speed) % 360
                ext3 = 70 + 30 * math.sin(t * 3.0 * speed)
                self.launcher_canvas.itemconfig(self._l_arc3, start=a3, extent=ext3, state=tk.NORMAL)
            elif state == "listening":
                a3 = (t * 80 * speed) % 360
                self.launcher_canvas.itemconfig(self._l_arc3, start=a3, extent=60, state=tk.NORMAL)
            else:
                self.launcher_canvas.itemconfig(self._l_arc3, state=tk.HIDDEN)

        # Core circle breathing
        if self._l_core is not None:
            breath = math.sin(t * 2.5 * speed)
            pad = 24 + breath * 2
            self.launcher_canvas.coords(self._l_core, pad, pad, size - pad, size - pad)

        # Orbiting particles at different radii, speeds, and pulsing sizes
        for i, pid in enumerate(self._l_particles):
            orbit_r = 38 - i * 4
            orbit_speed = (35 + i * 18) * speed
            phase_offset = i * (360.0 / len(self._l_particles))
            angle = math.radians((t * orbit_speed) % 360 + phase_offset)
            p_size = 3 + 1.5 * math.sin(t * (3.0 + i * 0.5) * speed + i)
            px = cx + orbit_r * math.cos(angle) - p_size / 2
            py = cy + orbit_r * math.sin(angle) - p_size / 2
            self.launcher_canvas.coords(pid, px, py, px + p_size, py + p_size)

        # Glow halo breathing
        if self._l_glow is not None:
            glow_pulse = 0.5 + 0.5 * math.sin(t * 1.5 * speed)
            gpad = 1 + glow_pulse * 3
            self.launcher_canvas.coords(self._l_glow, gpad, gpad, size - gpad, size - gpad)

    def _apply_launcher_colors(self: Any, colors: tuple[str, str, str, str, str, str]) -> None:
        """Apply a color palette to all launcher canvas elements."""
        assert self.launcher_canvas is not None
        arc1_c, arc2_c, core_c, particle_c, glow_c, arc4_c = colors
        if self._l_arc1 is not None:
            self.launcher_canvas.itemconfig(self._l_arc1, outline=arc1_c)
        if self._l_arc2 is not None:
            self.launcher_canvas.itemconfig(self._l_arc2, outline=arc2_c)
        if self._l_arc4 is not None:
            self.launcher_canvas.itemconfig(self._l_arc4, outline=arc4_c)
        if self._l_core is not None:
            self.launcher_canvas.itemconfig(self._l_core, fill=core_c, outline=arc1_c)
        for pid in self._l_particles:
            self.launcher_canvas.itemconfig(pid, fill=particle_c)
        if self._l_glow is not None:
            self.launcher_canvas.itemconfig(self._l_glow, outline=glow_c)

    def _animate_launcher(self: Any) -> None:
        if self.stop_event.is_set():
            return
        interval = self._launcher_animation_interval()
        try:
            if self.launcher_canvas is None:
                return
            if not getattr(self, "_launcher_visible", lambda: True)():
                self._launcher_after_id = self.after(interval, self._animate_launcher)
                return
            t = time.monotonic() - self._anim_t0
            speed = self._launcher_state_speed()
            snapshot = None
            ensure_controller = getattr(self, "_ensure_controller", None)
            if callable(ensure_controller):
                snapshot = ensure_controller().snapshot()

            self._update_launcher_geometry(t, speed)
            self._apply_launcher_colors(self._launcher_state_colors())
            if snapshot is not None:
                self._update_launcher_badge(snapshot)

            self._launcher_after_id = self.after(interval, self._animate_launcher)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Launcher animation stopped (widget may be destroyed)")
            return
