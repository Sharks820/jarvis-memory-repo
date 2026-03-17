"""Tests for VRAMCoordinator mutex and read_vram_used_mb."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.agent.vram_coordinator import (
    VRAMCoordinator,
    get_coordinator,
    read_vram_used_mb,
)


# ---------------------------------------------------------------------------
# VRAMCoordinator mutex behaviour
# ---------------------------------------------------------------------------


def test_status_initial_state() -> None:
    """Status shows no locks held on fresh instance."""
    coordinator = VRAMCoordinator()
    status = coordinator.status
    assert status["generation_active"] is False
    assert status["playmode_active"] is False
    assert status["locked"] is False


def test_acquire_generation_sets_status() -> None:
    """Acquiring generation lock marks generation_active=True."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_generation()
        status = coordinator.status
        assert status["generation_active"] is True
        assert status["playmode_active"] is False
        assert status["locked"] is True
        coordinator.release_generation()

    asyncio.run(_run())


def test_acquire_playmode_sets_status() -> None:
    """Acquiring playmode lock marks playmode_active=True."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_playmode()
        status = coordinator.status
        assert status["generation_active"] is False
        assert status["playmode_active"] is True
        assert status["locked"] is True
        coordinator.release_playmode()

    asyncio.run(_run())


def test_release_generation_clears_lock() -> None:
    """Releasing generation lock clears generation_active."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_generation()
        coordinator.release_generation()
        status = coordinator.status
        assert status["generation_active"] is False
        assert status["locked"] is False

    asyncio.run(_run())


def test_release_playmode_clears_lock() -> None:
    """Releasing playmode lock clears playmode_active."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_playmode()
        coordinator.release_playmode()
        status = coordinator.status
        assert status["playmode_active"] is False
        assert status["locked"] is False

    asyncio.run(_run())


def test_generation_blocks_playmode() -> None:
    """acquire_playmode should be blocked while generation is held."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_generation()

        # Attempt to acquire playmode with a short timeout — should timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(coordinator.acquire_playmode(), timeout=0.05)

        coordinator.release_generation()

    asyncio.run(_run())


def test_playmode_blocks_generation() -> None:
    """acquire_generation should be blocked while playmode is held."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_playmode()

        # Attempt to acquire generation with a short timeout — should timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(coordinator.acquire_generation(), timeout=0.05)

        coordinator.release_playmode()

    asyncio.run(_run())


def test_sequential_acquire_release_works() -> None:
    """After releasing, the other side can acquire successfully."""

    async def _run() -> None:
        coordinator = VRAMCoordinator()
        await coordinator.acquire_generation()
        coordinator.release_generation()
        # Now playmode can acquire
        await coordinator.acquire_playmode()
        coordinator.release_playmode()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_coordinator singleton
# ---------------------------------------------------------------------------


def test_get_coordinator_returns_singleton() -> None:
    """get_coordinator() returns the same instance on repeated calls."""
    c1 = get_coordinator()
    c2 = get_coordinator()
    assert c1 is c2


# ---------------------------------------------------------------------------
# read_vram_used_mb
# ---------------------------------------------------------------------------


def test_read_vram_used_mb_success() -> None:
    """Returns int MB when nvidia-smi succeeds."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "6144\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = read_vram_used_mb()
        assert result == 6144
        mock_run.assert_called_once()


def test_read_vram_used_mb_nonzero_returncode() -> None:
    """Returns None when nvidia-smi exits non-zero."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        result = read_vram_used_mb()
        assert result is None


def test_read_vram_used_mb_file_not_found() -> None:
    """Returns None when nvidia-smi is not installed (FileNotFoundError)."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = read_vram_used_mb()
        assert result is None


def test_read_vram_used_mb_timeout() -> None:
    """Returns None when nvidia-smi times out."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["nvidia-smi"], 5)):
        result = read_vram_used_mb()
        assert result is None


def test_read_vram_used_mb_invalid_output() -> None:
    """Returns None when nvidia-smi output is not parseable as int."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "N/A\n"

    with patch("subprocess.run", return_value=mock_result):
        result = read_vram_used_mb()
        assert result is None
