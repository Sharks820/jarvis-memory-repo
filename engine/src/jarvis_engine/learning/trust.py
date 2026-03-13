"""Trust classification helpers for always-on learning.

Phase 14-09A keeps learning always enabled while dual-writing trust metadata
in shadow mode. These helpers classify learned material without changing the
existing reasoning or retrieval behavior.
"""

from __future__ import annotations

import os
import re
from typing import TypedDict

from jarvis_engine._shared import now_iso, sha256_hex, sha256_short

TRUST_POLICY_AUDIT_ONLY = "audit_only"
TRUST_POLICY_WARN_ONLY = "warn_only"
TRUST_POLICY_APPROVAL_REQUIRED = "approval_required"
TRUST_POLICY_HARD_BLOCK = "hard_block"

_VALID_POLICY_MODES = {
    TRUST_POLICY_AUDIT_ONLY,
    TRUST_POLICY_WARN_ONLY,
    TRUST_POLICY_APPROVAL_REQUIRED,
    TRUST_POLICY_HARD_BLOCK,
}

TRUST_LEVEL_UNTRUSTED = "T0_untrusted"
TRUST_LEVEL_OBSERVED = "T1_observed"
TRUST_LEVEL_VERIFIED = "T2_verified"
TRUST_LEVEL_TRUSTED = "T3_trusted"
TRUST_LEVEL_BLOCKED = "T4_blocked"

PROMOTION_STATE_OBSERVED = "observed"
PROMOTION_STATE_QUARANTINED = "quarantined"
PROMOTION_STATE_VERIFIED = "verified"
PROMOTION_STATE_TRUSTED = "trusted"
PROMOTION_STATE_BLOCKED = "blocked"

LEARNING_LANE_TRUSTED = "trusted"
LEARNING_LANE_OBSERVED = "observed"
LEARNING_LANE_THREAT = "threat"

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_CODE_RE = re.compile(
    r"```|\b(def|class|import|from|return|SELECT|INSERT|UPDATE|DELETE|function|const|let|var)\b"
)
_SHELL_RE = re.compile(
    r"\b(curl|wget|powershell|bash|sh|chmod|sudo|pip install|npm install|Invoke-WebRequest)\b",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/[^\s]+)")
_PIPE_TO_SHELL_RE = re.compile(r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:bash|sh)\b", re.IGNORECASE)
_POWERSHELL_EXEC_RE = re.compile(
    r"\b(?:Invoke-WebRequest|iwr|curl)\b[^\n]{0,200}\b(?:iex|Invoke-Expression)\b|\bEncodedCommand\b",
    re.IGNORECASE,
)
_CREDENTIAL_PATTERNS = [
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(token|api[_-]?key|secret|signing[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(bearer)\s+\S+", re.IGNORECASE),
]


class LearningTrustMetadata(TypedDict):
    """Normalized trust metadata stored in shadow mode."""

    learning_lane: str
    trust_level: str
    promotion_state: str
    source_type: str
    source_channel: str
    source_uri: str
    source_hash: str
    artifact_kind: str
    mime_type: str
    scanner_verdict: str
    scanner_details: str
    approved_by_owner: bool
    approved_at: str
    correlation_id: str
    mission_id: str
    first_seen_at: str
    last_used_at: str
    promotion_reason: str
    blocked_reason: str
    derived_from_artifact: bool
    policy_mode: str


def learning_provenance_enabled() -> bool:
    """Return whether provenance dual-write is enabled."""
    raw = os.getenv("JARVIS_LEARNING_PROVENANCE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def trust_policy_mode() -> str:
    """Return the configured trust policy mode with a safe default."""
    raw = os.getenv("JARVIS_TRUST_POLICY_MODE", TRUST_POLICY_AUDIT_ONLY).strip().lower()
    return raw if raw in _VALID_POLICY_MODES else TRUST_POLICY_AUDIT_ONLY


def infer_artifact_kind(content: str, subject_type: str, tags: list[str] | None = None) -> str:
    """Infer the broad artifact kind for a learned subject."""
    if subject_type == "preference":
        return "preference_signal"
    if subject_type == "feedback":
        return "feedback_signal"
    if subject_type == "usage_pattern":
        return "usage_signal"
    if subject_type == "policy_event":
        return "policy_event"

    normalized_tags = {tag.strip().lower() for tag in (tags or []) if tag.strip()}
    lowered = content.lower()

    if "quarantine" in normalized_tags or "threat" in normalized_tags:
        return "threat_indicator"
    if _SHELL_RE.search(content):
        return "shell_command"
    if _CODE_RE.search(content):
        return "code"
    if _URL_RE.search(content):
        return "url"
    if _PATH_RE.search(content):
        return "file_reference"
    if any(tag in normalized_tags for tag in {"web", "research", "download"}):
        return "web_content"
    if lowered.endswith((".zip", ".exe", ".msi", ".py", ".sh", ".ps1", ".apk")):
        return "artifact_reference"
    return "text"


def _infer_source_type(subject_type: str, source_channel: str) -> str:
    normalized_channel = source_channel.strip().lower()
    if subject_type == "preference":
        return "preference_signal"
    if subject_type == "feedback":
        return "feedback_signal"
    if subject_type == "usage_pattern":
        return "usage_signal"
    if normalized_channel.startswith("conversation:user") or normalized_channel == "user":
        return "owner_input"
    if normalized_channel.startswith("conversation:assistant"):
        return "assistant_output"
    if normalized_channel in {"claude", "opus", "gemini"}:
        return "model_output"
    if normalized_channel == "task_outcome":
        return "task_outcome"
    if normalized_channel.startswith("web") or normalized_channel.startswith("http"):
        return "external_content"
    return "system_generated"


def _base_trust(source_type: str, artifact_kind: str) -> tuple[str, str, str, bool]:
    if source_type in {"owner_input", "preference_signal", "feedback_signal", "usage_signal"}:
        return LEARNING_LANE_TRUSTED, TRUST_LEVEL_TRUSTED, PROMOTION_STATE_TRUSTED, True
    if source_type == "task_outcome":
        return LEARNING_LANE_TRUSTED, TRUST_LEVEL_VERIFIED, PROMOTION_STATE_VERIFIED, False
    if artifact_kind == "threat_indicator":
        return LEARNING_LANE_THREAT, TRUST_LEVEL_BLOCKED, PROMOTION_STATE_BLOCKED, False
    return LEARNING_LANE_OBSERVED, TRUST_LEVEL_OBSERVED, PROMOTION_STATE_OBSERVED, False


def classify_learning_subject(
    *,
    subject_type: str,
    subject_id: str,
    source_channel: str,
    content: str,
    tags: list[str] | None = None,
    mission_id: str = "",
    source_uri: str = "",
) -> LearningTrustMetadata:
    """Build trust metadata for a learned subject.

    This is intentionally conservative: external/model-generated material stays
    in the observed lane until a later promotion phase exists.
    """
    normalized_tags = [tag.strip().lower() for tag in (tags or []) if tag.strip()]
    source_type = _infer_source_type(subject_type, source_channel)
    artifact_kind = infer_artifact_kind(content, subject_type, tags=normalized_tags)
    learning_lane, trust_level, promotion_state, approved_by_owner = _base_trust(
        source_type, artifact_kind,
    )
    now = now_iso()
    source_hash = sha256_hex(content or f"{subject_type}:{subject_id}:{source_channel}")
    correlation_id = sha256_short(
        f"{subject_type}|{subject_id}|{source_channel}|{mission_id}|{source_hash}".encode("utf-8")
    )
    mime_type = "text/plain"
    if artifact_kind in {"code", "shell_command"}:
        mime_type = "text/x-script"

    derived_from_artifact = artifact_kind in {
        "artifact_reference", "code", "shell_command", "url", "web_content", "file_reference",
    }
    scanner_verdict = "not_required" if approved_by_owner else "shadow_unscanned"

    return {
        "learning_lane": learning_lane,
        "trust_level": trust_level,
        "promotion_state": promotion_state,
        "source_type": source_type,
        "source_channel": source_channel,
        "source_uri": source_uri,
        "source_hash": source_hash,
        "artifact_kind": artifact_kind,
        "mime_type": mime_type,
        "scanner_verdict": scanner_verdict,
        "scanner_details": "phase_14_09a_shadow_mode",
        "approved_by_owner": approved_by_owner,
        "approved_at": now if approved_by_owner else "",
        "correlation_id": correlation_id,
        "mission_id": mission_id,
        "first_seen_at": now,
        "last_used_at": now,
        "promotion_reason": "shadow_auto_classification",
        "blocked_reason": "" if promotion_state != PROMOTION_STATE_BLOCKED else "threat_indicator",
        "derived_from_artifact": derived_from_artifact,
        "policy_mode": trust_policy_mode(),
    }


def artifact_requires_quarantine(metadata: LearningTrustMetadata) -> bool:
    """Return True when a subject should enter the shadow quarantine lane."""
    return (
        metadata["derived_from_artifact"]
        and not metadata["approved_by_owner"]
        and metadata["trust_level"] in {TRUST_LEVEL_UNTRUSTED, TRUST_LEVEL_OBSERVED}
    )


def safe_artifact_summary(content: str, max_chars: int = 240) -> str:
    """Return a redacted, bounded preview safe for quarantine telemetry."""
    summary = content.strip()
    for pattern in _CREDENTIAL_PATTERNS:
        summary = pattern.sub(r"\1=[REDACTED]", summary)
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars] + "...(truncated)"


def detect_threat_indicators(content: str, tags: list[str] | None = None) -> list[str]:
    """Return deterministic threat markers for obviously dangerous patterns."""
    indicators: list[str] = []
    normalized_tags = {tag.strip().lower() for tag in (tags or []) if tag.strip()}

    if "threat" in normalized_tags or "quarantine" in normalized_tags:
        indicators.append("tagged_threat_content")
    if _PIPE_TO_SHELL_RE.search(content):
        indicators.append("pipe_to_shell_download")
    if _POWERSHELL_EXEC_RE.search(content):
        indicators.append("powershell_download_execute")
    return indicators
