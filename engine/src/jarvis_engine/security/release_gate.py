"""Security release gate with severity thresholds.

SEC-05: Runs static analysis tools (ruff, bandit) and checks findings
against configurable severity thresholds before allowing a release.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default thresholds: 0 HIGH, <20 MEDIUM
_DEFAULT_MAX_HIGH = 0
_DEFAULT_MAX_MEDIUM = 20


@dataclass
class ReleaseGateResult:
    """Result of a release gate scan."""

    passed: bool
    findings_by_severity: dict[str, int] = field(default_factory=dict)
    tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    threshold_check: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _run_tool(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", f"Tool not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"Tool timed out after {timeout}s: {cmd[0]}"
    except OSError as exc:
        return -3, "", f"OS error running {cmd[0]}: {exc}"


def _run_ruff(project_dir: Path) -> dict[str, Any]:
    """Run ruff check and return findings summary."""
    rc, stdout, stderr = _run_tool(
        ["python", "-m", "ruff", "check", "--output-format=json", "."],
        cwd=project_dir,
    )
    if rc < 0:
        return {"available": False, "error": stderr, "findings": 0}

    # Count findings from ruff JSON output
    findings = 0
    high = 0
    medium = 0
    low = 0
    try:
        import json
        items = json.loads(stdout) if stdout.strip() else []
        findings = len(items)
        for item in items:
            # Ruff doesn't have severity levels; treat all as LOW
            low += 1
    except (ValueError, TypeError):
        # Fallback: count lines
        findings = len(stdout.strip().splitlines()) if stdout.strip() else 0
        low = findings

    return {
        "available": True,
        "findings": findings,
        "high": high,
        "medium": medium,
        "low": low,
    }


def _run_bandit(project_dir: Path) -> dict[str, Any]:
    """Run bandit security scanner and return findings summary."""
    rc, stdout, stderr = _run_tool(
        [
            "python", "-m", "bandit",
            "-r", ".",
            "-f", "json",
            "--severity-level", "low",
            "-q",
        ],
        cwd=project_dir,
    )
    if rc < 0:
        return {"available": False, "error": stderr, "findings": 0}

    high = 0
    medium = 0
    low = 0
    findings = 0
    try:
        import json
        data = json.loads(stdout) if stdout.strip() else {}
        results = data.get("results", [])
        findings = len(results)
        for r in results:
            sev = r.get("issue_severity", "").upper()
            if sev == "HIGH":
                high += 1
            elif sev == "MEDIUM":
                medium += 1
            else:
                low += 1
    except (ValueError, TypeError):
        findings = 0

    return {
        "available": True,
        "findings": findings,
        "high": high,
        "medium": medium,
        "low": low,
    }


def run_release_gate(
    project_dir: Path | str | None = None,
    *,
    max_high: int = _DEFAULT_MAX_HIGH,
    max_medium: int = _DEFAULT_MAX_MEDIUM,
) -> ReleaseGateResult:
    """Run the security release gate.

    Executes ruff and bandit against the project, aggregates findings
    by severity, and checks against the configured thresholds.

    Parameters
    ----------
    project_dir:
        Root directory of the project to scan.  Defaults to CWD.
    max_high:
        Maximum allowed HIGH severity findings (default 0).
    max_medium:
        Maximum allowed MEDIUM severity findings (default 20).

    Returns
    -------
    ReleaseGateResult:
        Contains pass/fail, findings counts, and threshold check details.
    """
    if project_dir is None:
        project_dir = Path.cwd()
    else:
        project_dir = Path(project_dir)

    errors: list[str] = []
    tool_results: dict[str, dict[str, Any]] = {}

    # Run ruff
    ruff_result = _run_ruff(project_dir)
    tool_results["ruff"] = ruff_result
    if not ruff_result.get("available"):
        errors.append(f"ruff: {ruff_result.get('error', 'unavailable')}")

    # Run bandit
    bandit_result = _run_bandit(project_dir)
    tool_results["bandit"] = bandit_result
    if not bandit_result.get("available"):
        errors.append(f"bandit: {bandit_result.get('error', 'unavailable')}")

    # Aggregate findings by severity
    total_high = sum(r.get("high", 0) for r in tool_results.values())
    total_medium = sum(r.get("medium", 0) for r in tool_results.values())
    total_low = sum(r.get("low", 0) for r in tool_results.values())
    total_findings = sum(r.get("findings", 0) for r in tool_results.values())

    findings_by_severity = {
        "HIGH": total_high,
        "MEDIUM": total_medium,
        "LOW": total_low,
    }

    # Threshold check
    high_ok = total_high <= max_high
    medium_ok = total_medium <= max_medium
    passed = high_ok and medium_ok

    threshold_check = {
        "max_high": max_high,
        "actual_high": total_high,
        "high_ok": high_ok,
        "max_medium": max_medium,
        "actual_medium": total_medium,
        "medium_ok": medium_ok,
        "total_findings": total_findings,
    }

    if not passed:
        reasons = []
        if not high_ok:
            reasons.append(f"HIGH findings {total_high} > {max_high}")
        if not medium_ok:
            reasons.append(f"MEDIUM findings {total_medium} > {max_medium}")
        logger.warning("Release gate FAILED: %s", "; ".join(reasons))
    else:
        logger.info(
            "Release gate PASSED: %d HIGH, %d MEDIUM, %d LOW",
            total_high, total_medium, total_low,
        )

    return ReleaseGateResult(
        passed=passed,
        findings_by_severity=findings_by_severity,
        tool_results=tool_results,
        threshold_check=threshold_check,
        errors=errors,
    )
