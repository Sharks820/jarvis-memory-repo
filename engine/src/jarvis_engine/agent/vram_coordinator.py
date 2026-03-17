"""VRAM coordinator mutex -- prevents concurrent Ollama inference and Unity play-mode GPU use.

The RTX 4060 Ti has 8 GB VRAM.  Ollama qwen3.5 Q4_K_M needs ~6.3 GB (weights +
KV cache).  Unity play-mode rendering needs ~1-3 GB.  There is zero margin for
simultaneous use.  This module provides a single asyncio.Lock (``_gpu_mutex``)
that makes ``generation_active`` and ``unity_playmode_active`` mutually exclusive.

Usage::

    from jarvis_engine.agent.vram_coordinator import get_coordinator

    coordinator = get_coordinator()
    await coordinator.acquire_generation()
    try:
        result = await gateway.complete(...)
    finally:
        coordinator.release_generation()
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Threshold: if VRAM usage (MB) exceeds this, log a warning before acquiring.
VRAM_PRESSURE_THRESHOLD_MB = 7500


@dataclass
class VRAMCoordinator:
    """Mutex preventing concurrent Ollama inference and Unity play-mode GPU use.

    ``generation_active`` and ``unity_playmode_active`` are mutually exclusive.
    Acquire ``generation`` before any ModelGateway call.
    Acquire ``playmode`` before any Unity play-mode entry command.
    Both share ``_gpu_mutex`` — only one may be held at a time.
    """

    _gpu_mutex: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _generation_active: bool = field(default=False, init=False)
    _playmode_active: bool = field(default=False, init=False)

    async def acquire_generation(self) -> None:
        """Acquire the GPU mutex for an Ollama generation call.

        Blocks until no Unity play-mode is active.
        """
        await self._gpu_mutex.acquire()
        self._generation_active = True
        logger.debug("VRAMCoordinator: generation_active=True")

    def release_generation(self) -> None:
        """Release the GPU mutex after generation completes."""
        self._generation_active = False
        self._gpu_mutex.release()
        logger.debug("VRAMCoordinator: generation_active=False")

    async def acquire_playmode(self) -> None:
        """Acquire the GPU mutex before entering Unity play-mode.

        Blocks until no Ollama generation is active.
        """
        await self._gpu_mutex.acquire()
        self._playmode_active = True
        logger.debug("VRAMCoordinator: unity_playmode_active=True")

    def release_playmode(self) -> None:
        """Release the GPU mutex after Unity play-mode exits."""
        self._playmode_active = False
        self._gpu_mutex.release()
        logger.debug("VRAMCoordinator: unity_playmode_active=False")

    @property
    def status(self) -> dict[str, bool]:
        """Return current coordinator state as a dict."""
        return {
            "generation_active": self._generation_active,
            "playmode_active": self._playmode_active,
            "locked": self._gpu_mutex.locked(),
        }


def read_vram_used_mb() -> int | None:
    """Query nvidia-smi for current VRAM usage in MB.

    Returns the integer MB value on success, or ``None`` on any failure
    (nvidia-smi not found, timeout, non-zero exit, unparseable output).

    This is a synchronous utility intended for checkpoint boundaries only --
    do NOT call it on an async hot path.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip().split("\n")[0])
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_COORDINATOR: VRAMCoordinator | None = None


def get_coordinator() -> VRAMCoordinator:
    """Return the process-wide VRAMCoordinator singleton.

    Creates a new instance on first call.  Thread-safe for reads; callers
    must not reset ``_COORDINATOR`` from outside this module.
    """
    global _COORDINATOR  # noqa: PLW0603
    if _COORDINATOR is None:
        _COORDINATOR = VRAMCoordinator()
    return _COORDINATOR
