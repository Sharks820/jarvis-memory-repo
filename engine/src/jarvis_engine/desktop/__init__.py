"""Desktop widget subpackage — re-exports for backward compatibility."""

from jarvis_engine.desktop.controller import (
    DesktopInteractionController,
    DesktopWidgetState,
)
from jarvis_engine.desktop.widget import JarvisDesktopWidget, run_desktop_widget
from jarvis_engine.desktop.helpers import WidgetConfig

__all__ = [
    "DesktopInteractionController",
    "DesktopWidgetState",
    "JarvisDesktopWidget",
    "WidgetConfig",
    "run_desktop_widget",
]
