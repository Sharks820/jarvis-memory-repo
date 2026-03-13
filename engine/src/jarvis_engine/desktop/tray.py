"""System tray icon management for the Jarvis desktop widget.

Contains the ``TrayMixin`` class that provides system tray icon
creation, menu callbacks, and lifecycle management using ``pystray``.
Extracted from ``desktop_widget.py`` to improve separation of concerns.

Classes and methods in this module are intended to be mixed into
``JarvisDesktopWidget`` via multiple inheritance; they should NOT be
used standalone.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Any

from jarvis_engine.widget_helpers import _create_tray_icon_image

logger = logging.getLogger(__name__)


class TrayMixin:
    """Mixin providing system tray icon management via pystray.

    Expects the host class to supply the following attributes:
      - ``self._tray_icon`` (initialized to ``None``)
      - ``self.after()`` (tkinter scheduling)
      - ``self._show_panel()``, ``self._voice_dictate()``,
        ``self._send_text()``, ``self._confirm_exit()``
    """

    def _build_tray_icon(self: Any) -> None:
        """Create a system tray icon using pystray. Runs in a daemon thread."""
        try:
            import pystray  # type: ignore[import-not-found]  # noqa: E402
        except ImportError:
            logger.info("pystray or Pillow not installed; system tray icon disabled")
            return

        # Create a 64x64 icon: blue circle with white "J"
        image = _create_tray_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("Show Widget", self._tray_show_widget, default=True),
            pystray.MenuItem("Voice Dictate", self._tray_voice_dictate),
            pystray.MenuItem("Ops Brief", self._tray_ops_brief),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )

        icon = pystray.Icon("jarvis", image, "Jarvis Unlimited", menu)
        self._tray_icon = icon
        # pystray.Icon.run() blocks, so run in a daemon thread
        tray_thread = threading.Thread(target=icon.run, daemon=True)
        tray_thread.start()

    def _tray_show_widget(self: Any, icon: Any = None, item: Any = None) -> None:
        """Tray menu: Show Widget (also handles double-click)."""
        try:
            self.after(0, self._show_panel)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Tray show-widget failed (widget may be destroyed)")

    def _tray_voice_dictate(self: Any, icon: Any = None, item: Any = None) -> None:
        """Tray menu: Voice Dictate."""
        try:
            self.after(0, self._show_panel)
            self.after(100, self._dictate_async)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Tray voice-dictate failed (widget may be destroyed)")

    def _tray_ops_brief(self: Any, icon: Any = None, item: Any = None) -> None:
        """Tray menu: Ops Brief."""
        try:
            self.after(0, self._show_panel)
            self.after(100, lambda: self._send_text("ops brief"))
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Tray ops-brief failed (widget may be destroyed)")

    def _tray_quit(self: Any, icon: Any = None, item: Any = None) -> None:
        """Tray menu: Quit."""
        try:
            self.after(0, self._confirm_exit)
        except (tk.TclError, RuntimeError):  # Widget may be destroyed
            logger.debug("Tray quit failed (widget may be destroyed)")

    def _stop_tray_icon(self: Any) -> None:
        """Stop the tray icon cleanly."""
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception as exc:  # boundary: catch-all justified
                logger.debug("Failed to stop tray icon: %s", exc)
            self._tray_icon = None
